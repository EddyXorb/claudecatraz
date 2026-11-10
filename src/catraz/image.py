import hashlib
import json
import subprocess
import urllib.request
from pathlib import Path
from catraz.envfile import load_env
from catraz.errors import CliError, EXIT_DOCKER

_CLAUDE_CODE_LATEST_URL = "https://registry.npmjs.org/@anthropic-ai/claude-code/latest"


def pinned_claude_code_version(root: Path) -> str | None:
    """CLAUDE_CODE_VERSION from .catraz/.env, or None when unset or "latest"."""
    configured = load_env(root / ".catraz/.env").get("CLAUDE_CODE_VERSION", "").strip()
    return configured if configured and configured != "latest" else None


def published_claude_code_version() -> str | None:
    """Currently published claude-code version, or None when the registry is unreachable."""
    try:
        with urllib.request.urlopen(_CLAUDE_CODE_LATEST_URL, timeout=5) as resp:
            return str(json.load(resp)["version"]) or None
    except (OSError, ValueError, KeyError):
        return None


def resolve_claude_code_version(root: Path) -> str:
    """Concrete claude-code version for the image build arg: the pinned one, else the
    published one, so a new release changes the build arg and busts the layer cache.
    Falls back to "latest" when the registry is unreachable (the build still works)."""
    return pinned_claude_code_version(root) or published_claude_code_version() or "latest"


def _image_exists(tag: str) -> bool:
    return subprocess.run(["docker", "image", "inspect", tag], capture_output=True).returncode == 0


def _build_base(dockerfile: Path, context: Path | None = None) -> str:
    ctx = context or dockerfile.parent
    tag = f"catraz-base:{hashlib.sha256(dockerfile.read_bytes()).hexdigest()[:12]}"
    if not _image_exists(tag):
        r = subprocess.run(["docker", "build", "-t", tag, "-f", str(dockerfile), str(ctx)])
        if r.returncode:
            raise CliError(
                f"base build failed (Dockerfile {dockerfile}). "
                "catraz's agent layer (assets/agents/<profile>/layer.Dockerfile) "
                "uses apt-get and NodeSource — "
                "the base MUST be Debian/Ubuntu-based. "
                "Alpine, RHEL, and other non-apt distros will fail here.",
                EXIT_DOCKER,
            )
    return tag


def resolve_base(root: Path) -> str:
    env: dict[str, str] = load_env(root / ".catraz/.env")
    if env.get("BASE_IMAGE"):
        return env["BASE_IMAGE"]
    if env.get("BASE_DOCKERFILE"):
        df = (root / env["BASE_DOCKERFILE"]).resolve()
        if not df.exists():
            raise CliError(f"BASE_DOCKERFILE not found: {df}", EXIT_DOCKER)
        ctx = None
        if env.get("BASE_CONTEXT"):
            ctx = (root / env["BASE_CONTEXT"]).resolve()
            if not ctx.is_dir():
                raise CliError(f"BASE_CONTEXT not a directory: {ctx}", EXIT_DOCKER)
        return _build_base(df, ctx)
    # Default: local user-owned Dockerfile seeded by `catraz init`.
    df = root / ".catraz" / "config" / "image" / "Dockerfile"
    if not df.exists():
        raise CliError(
            f"base Dockerfile not found: {df}. "
            "Run `catraz init` to seed it, or set BASE_IMAGE / BASE_DOCKERFILE in .catraz/.env.",
            EXIT_DOCKER,
        )
    return _build_base(df)
