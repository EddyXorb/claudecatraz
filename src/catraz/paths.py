"""Asset + project-root resolution."""
import importlib.resources as ir
import os
import shutil
from pathlib import Path

from catraz import __version__


def _repo_root() -> Path | None:
    # Zero-install: this file lives at <repo>/src/catraz/paths.py
    here = Path(__file__).resolve()
    cand = here.parents[2]
    return cand if (cand / "pyproject.toml").exists() else None


def asset_root() -> Path:
    """Deterministically extract packaged assets to a versioned cache and return it.
    Build contexts and compose files are read from here, never from the venv/CWD."""
    dst = Path.home() / ".cache" / "catraz" / __version__
    marker = dst / ".extracted"
    if marker.exists():
        return dst
    (dst / "assets").mkdir(parents=True, exist_ok=True)
    pkg_assets = ir.files("catraz") / "assets"
    if pkg_assets.is_dir():  # installed wheel
        with ir.as_file(pkg_assets) as src:
            shutil.copytree(src, dst / "assets", dirs_exist_ok=True)
    else:  # zero-install source tree: assets under src/, contexts at repo root
        repo = _repo_root()
        assert repo, "cannot locate assets"
        shutil.copytree(repo / "src" / "catraz" / "assets", dst / "assets", dirs_exist_ok=True)
        for ctx in ("warden", "forward-proxy"):
            shutil.copytree(repo / ctx, dst / "assets" / ctx, dirs_exist_ok=True)
    marker.write_text("")
    return dst


def find_root(explicit):
    """Project root = the dir containing docker-compose.yml (searched upward)."""
    from catraz.cli import CliError, EXIT_CONFIG
    if explicit:
        root = Path(explicit).resolve()
        if not (root / "docker-compose.yml").exists():
            raise CliError(f"no docker-compose.yml in {root}", EXIT_CONFIG)
        return root
    here = Path.cwd().resolve()
    for d in (here, *here.parents):
        if (d / "docker-compose.yml").exists():
            return d
    # Fall back to the script's own directory (zero-install ./catraz case).
    here = Path(__file__).resolve().parent
    if (here / "docker-compose.yml").exists():
        return here
    raise CliError("no docker-compose.yml found (use -C/--dir)", EXIT_CONFIG)


def _claude_home(root, env):
    raw = env.get("CLAUDE_HOME", "./claude")
    p = Path(os.path.expanduser(raw))
    return p if p.is_absolute() else (root / p).resolve()
