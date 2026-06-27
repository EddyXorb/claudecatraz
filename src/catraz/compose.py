"""docker-compose call + invariants."""
import json
import os
import subprocess
from pathlib import Path

from catraz.paths import asset_root


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
