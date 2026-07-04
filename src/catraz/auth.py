from pathlib import Path
from catraz.envfile import load_env
from catraz.errors import CliError, EXIT_CONFIG


def auth_mode(root: Path) -> str:
    mode: str = load_env(root / ".catraz/.env").get("AUTH_MODE", "subscription")
    if mode not in ("subscription", "api_key"):
        raise CliError(f"AUTH_MODE must be subscription|api_key, got {mode!r}", EXIT_CONFIG)
    return mode
