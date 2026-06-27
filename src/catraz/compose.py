"""docker-compose call + invariants."""
import json
import os
import subprocess
from pathlib import Path

from catraz.paths import asset_root
from catraz.errors import CliError, EXIT_CONFIG


# Semantic service aliases → compose service names (P1: no raw container names).
SERVICES = {
    "agent": "claude-dev-env",
    "warden": "gitlab-warden",
    "proxy": "forward-proxy",
}


def base_cmd(root: Path) -> list[str]:
    ar = asset_root()
    cmd = ["docker", "compose",
           "-f", str(ar / "assets/compose/docker-compose.yml"),
           "--project-directory", str(root),
           "--env-file", str(root / ".catraz/.env")]
    frag = root / ".catraz/.auth.compose.yml"
    if frag.exists():
        cmd += ["-f", str(frag)]
    override = root / ".catraz/compose.override.yml"
    if override.exists():
        cmd += ["-f", str(override)]
    return cmd


def run(root: Path, args, capture=False, check=True, print_only=False):
    from catraz.errors import CliError, EXIT_DOCKER
    cmd = [*base_cmd(root), *args]
    if print_only:
        print(" ".join(cmd))
        return None
    env = dict(os.environ, PROJECT_DIR=str(root))
    try:
        return subprocess.run(cmd, env=env, check=check, capture_output=capture, text=True)
    except FileNotFoundError:
        raise CliError("`docker` not found on PATH", EXIT_DOCKER)


def compose_ps(root):
    """Return [{Service, State, Health}, …] from `docker compose ps`."""
    r = run(root, ["ps", "--format", "json"], capture=True, check=False)
    if r is None or r.returncode != 0 or not r.stdout.strip():
        return []
    text = r.stdout.strip()
    try:  # some compose versions emit a JSON array, others one object per line
        data = json.loads(text)
        rows = data if isinstance(data, list) else [data]
    except json.JSONDecodeError:
        rows = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
    return rows


def resolve_service(name):
    from catraz.errors import CliError, EXIT_CONFIG
    if name in SERVICES:
        return SERVICES[name]
    if name in SERVICES.values():
        return name
    raise CliError(
        f"unknown service '{name}' — use one of: {', '.join(SERVICES)}", EXIT_CONFIG
    )


def assert_real_dirs(root) -> None:
    for p in (root, root / ".catraz"):
        if p.is_symlink():
            raise CliError(f"{p} is a symlink — bind source must be a real dir", EXIT_CONFIG)


def _env_keys(agent) -> set:
    env = agent.get("environment") or {}
    if isinstance(env, list):
        return {e.split("=", 1)[0] for e in env}
    return set(env.keys())


def assert_invariants(root) -> None:
    r = run(root, ["config", "--format", "json"], capture=True, check=False)
    if r is None or r.returncode != 0:
        raise CliError("docker compose config failed (cannot verify trust boundary)", EXIT_CONFIG)
    cfg = json.loads(r.stdout)
    if not cfg.get("networks", {}).get("agent-net", {}).get("internal"):
        raise CliError("invariant: agent-net is not internal", EXIT_CONFIG)
    agent = cfg["services"]["claude-dev-env"]
    if any(k.startswith("GITLAB_") and k.endswith("_TOKEN") for k in _env_keys(agent)):
        raise CliError("invariant: agent carries a GITLAB_*_TOKEN", EXIT_CONFIG)
    if agent.get("privileged") or "SYS_ADMIN" in (agent.get("cap_add") or []):
        raise CliError("invariant: agent is privileged / CAP_SYS_ADMIN", EXIT_CONFIG)
    vols = agent.get("volumes", [])
    if not any(v.get("type") == "tmpfs" and v.get("target") == "/workspace/.catraz" for v in vols):
        raise CliError("invariant: tmpfs shadow on /workspace/.catraz missing", EXIT_CONFIG)
