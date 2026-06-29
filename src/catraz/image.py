import hashlib, subprocess
from pathlib import Path
from catraz.paths import asset_root
from catraz.envfile import load_env
from catraz.errors import CliError, EXIT_DOCKER

def _image_exists(tag: str) -> bool:
    return subprocess.run(["docker", "image", "inspect", tag],
                          capture_output=True).returncode == 0

def _build_base(dockerfile: Path, context: Path | None = None) -> str:
    ctx = context or dockerfile.parent
    tag = f"catraz-base:{hashlib.sha256(dockerfile.read_bytes()).hexdigest()[:12]}"
    if not _image_exists(tag):
        r = subprocess.run(["docker", "build", "-t", tag,
                            "-f", str(dockerfile), str(ctx)])
        if r.returncode:
            raise CliError(
                f"base build failed (Dockerfile {dockerfile}). "
                "catraz's claude-layer uses apt-get and NodeSource — "
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
