from pathlib import Path
from catraz.envfile import load_env
from catraz.errors import CliError, EXIT_CONFIG

SUBSCRIPTION_FRAGMENT = """\
services:
  claude-dev-env:
    volumes:
      - type: bind
        source: ${PROJECT_DIR}/.catraz/claude/.credentials.json
        target: /home/dev/.claude/.ro/.credentials.json
        read_only: true
      - type: bind
        source: ${PROJECT_DIR}/.catraz/claude/.claude.json
        target: /home/dev/.claude/.ro/.claude.json
        read_only: true
"""
API_KEY_FRAGMENT = """\
services:
  claude-dev-env:
    environment:
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
"""

def auth_mode(root: Path) -> str:
    mode = load_env(root / ".catraz/.env").get("AUTH_MODE", "subscription")
    if mode not in ("subscription", "api_key"):
        raise CliError(f"AUTH_MODE must be subscription|api_key, got {mode!r}", EXIT_CONFIG)
    return mode

def write_auth_fragment(root: Path) -> Path:
    frag = root / ".catraz/.auth.compose.yml"
    frag.write_text(SUBSCRIPTION_FRAGMENT if auth_mode(root) == "subscription" else API_KEY_FRAGMENT)
    return frag
