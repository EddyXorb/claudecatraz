"""Asset + project-root resolution."""
import importlib.resources as ir
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
