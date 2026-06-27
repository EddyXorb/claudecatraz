"""docker-compose call + invariants."""
import json
import subprocess
from pathlib import Path


# Semantic service aliases → compose service names (P1: no raw container names).
SERVICES = {
    "agent": "claude-dev-env",
    "warden": "gitlab-warden",
    "proxy": "forward-proxy",
}


def compose(root, args, print_only=False, capture=False, check=True):
    from catraz.cli import CliError, EXIT_DOCKER
    cmd = ["docker", "compose", *args]
    if print_only:
        print(" ".join(cmd))
        return None
    try:
        return subprocess.run(
            cmd, cwd=root, check=check,
            capture_output=capture, text=True,
        )
    except FileNotFoundError:
        raise CliError("`docker` not found on PATH", EXIT_DOCKER)


def compose_ps(root):
    """Return [{Service, State, Health}, …] from `docker compose ps`."""
    r = compose(root, ["ps", "--format", "json"], capture=True, check=False)
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
    from catraz.cli import CliError, EXIT_CONFIG
    if name in SERVICES:
        return SERVICES[name]
    if name in SERVICES.values():
        return name
    raise CliError(
        f"unknown service '{name}' — use one of: {', '.join(SERVICES)}", EXIT_CONFIG
    )
