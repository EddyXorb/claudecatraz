"""Asset + project-root resolution."""
import importlib.resources as ir
import os
import shutil
from pathlib import Path

from catraz import __version__
from catraz.errors import CliError, EXIT_CONFIG


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
    if pkg_assets.is_dir() and (pkg_assets / "warden").is_dir():  # installed wheel
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


def find_root(explicit: str | None = None) -> Path:
    """Project root = the dir containing a `.catraz/` (searched upward, like git)."""
    if explicit:
        root = Path(explicit).resolve()
        if not (root / ".catraz").is_dir():
            raise CliError(f"no .catraz in {root}", EXIT_CONFIG)
        _assert_no_nested(root)
        return root
    here = Path.cwd().resolve()
    for d in (here, *here.parents):
        if (d / ".catraz").is_dir():
            _assert_no_nested(d)
            return d
    raise CliError("no .catraz found (run `catraz init`)", EXIT_CONFIG)


def _assert_no_nested(root: Path) -> None:
    top = root / ".catraz"
    for dirpath, dirnames, _ in os.walk(root):
        if ".catraz" in dirnames and Path(dirpath) / ".catraz" != top:
            raise CliError(f"nested .catraz at {Path(dirpath)/'.catraz'} — refuse", EXIT_CONFIG)
        dirnames[:] = [d for d in dirnames
                       if Path(dirpath) / d not in (top, root / ".git")]


def claude_home(root: Path) -> Path:
    return root / ".catraz" / "claude"
