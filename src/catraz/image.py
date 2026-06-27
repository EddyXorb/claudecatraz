import hashlib, subprocess
from pathlib import Path
from catraz.paths import asset_root
from catraz.envfile import load_env
from catraz.errors import CliError, EXIT_DOCKER

def _image_exists(tag: str) -> bool:
    return subprocess.run(["docker", "image", "inspect", tag],
                          capture_output=True).returncode == 0

def _build_base(dockerfile: Path) -> str:
    tag = f"catraz-base:{hashlib.sha256(dockerfile.read_bytes()).hexdigest()[:12]}"
    if not _image_exists(tag):
        r = subprocess.run(["docker", "build", "-t", tag,
                            "-f", str(dockerfile), str(dockerfile.parent)])
        if r.returncode:
            raise CliError(f"base build failed (Dockerfile {dockerfile})", EXIT_DOCKER)
    return tag

def resolve_base(root: Path) -> str:
    env = load_env(root / ".catraz/.env")
    if env.get("BASE_IMAGE"):
        return env["BASE_IMAGE"]
    if env.get("BASE_DOCKERFILE"):
        df = (root / env["BASE_DOCKERFILE"]).resolve()
        if not df.exists():
            raise CliError(f"BASE_DOCKERFILE not found: {df}", EXIT_DOCKER)
        return _build_base(df)
    return _build_base(asset_root() / "assets/bases/cpp-rust-python/Dockerfile")

def prune() -> None:
    r = subprocess.run(["docker", "image", "ls", "catraz-base", "--format", "{{.Repository}}:{{.Tag}}"],
                       capture_output=True, text=True)
    for tag in r.stdout.split():
        subprocess.run(["docker", "image", "rm", tag], capture_output=True)
