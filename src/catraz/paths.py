"""Asset + project-root resolution."""

import importlib.resources as ir
import os
import shutil
from pathlib import Path

from catraz import __version__
from catraz.errors import CliError, EXIT_CONFIG


def _cache_root() -> Path:
    base = (
        os.environ.get("CATRAZ_CACHE_DIR")
        or os.environ.get("XDG_CACHE_HOME")
        or str(Path.home() / ".cache")
    )
    return Path(base).expanduser() / "catraz" / __version__


def _repo_root() -> Path | None:
    # Zero-install: this file lives at <repo>/src/catraz/paths.py
    here = Path(__file__).resolve()
    cand = here.parents[2]
    return cand if (cand / "pyproject.toml").exists() else None


_IGNORE = shutil.ignore_patterns(".venv", "__pycache__", "*.pyc", ".git", "*.egg-info")


def _source_signature(*roots: Path) -> str:
    newest = 0.0
    for r in roots:
        if not r.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(r):
            dirnames[:] = [d for d in dirnames if d not in (".venv", "__pycache__", ".git")]
            for name in filenames:
                if name.endswith(".pyc"):
                    continue
                try:
                    newest = max(newest, (Path(dirpath) / name).stat().st_mtime)
                except OSError:
                    pass
    return repr(newest)


def _installed_sig() -> str:
    """Signature derived from the installed wheel's RECORD — changes on every reinstall."""
    try:
        from importlib.metadata import distribution

        record = distribution("catraz").read_text("RECORD") or ""
        return repr(hash(record))
    except Exception:
        return ""


def asset_root() -> Path:
    """Deterministically extract packaged assets to a versioned cache and return it.
    Build contexts and compose files are read from here, never from the venv/CWD."""
    dst = _cache_root()
    marker = dst / ".extracted"
    repo = _repo_root()
    sig = (
        _source_signature(repo / "src/catraz/assets", repo / "warden", repo / "forward-proxy")
        if repo
        else _installed_sig()
    )
    if marker.exists():
        if marker.read_text() == sig:  # source/wheel unchanged → trust cache
            return dst
        shutil.rmtree(dst / "assets", ignore_errors=True)  # stale cache → rebuild clean
    (dst / "assets").mkdir(parents=True, exist_ok=True)
    pkg_assets = ir.files("catraz") / "assets"
    if pkg_assets.is_dir() and (pkg_assets / "warden").is_dir():  # installed wheel
        with ir.as_file(pkg_assets) as src:
            shutil.copytree(src, dst / "assets", dirs_exist_ok=True)
    else:  # zero-install source tree
        assert repo, "cannot locate assets"
        shutil.copytree(
            repo / "src/catraz/assets",
            dst / "assets",
            dirs_exist_ok=True,
            ignore=_IGNORE,
        )
        for ctx in ("warden", "forward-proxy"):
            shutil.copytree(repo / ctx, dst / "assets" / ctx, dirs_exist_ok=True, ignore=_IGNORE)
    marker.write_text(sig)  # store the signature we just extracted
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
            raise CliError(f"nested .catraz at {Path(dirpath) / '.catraz'} — refuse", EXIT_CONFIG)
        dirnames[:] = [d for d in dirnames if Path(dirpath) / d not in (top, root / ".git")]


def claude_home(root: Path) -> Path:
    """Default subscription-sync credential dir; see agent_state_dir for the
    profile-generic persistent-state counterpart."""
    return root / ".catraz" / "secrets" / "claude"


def agent_state_dir(root: Path, profile: str) -> Path:
    """Writable per-repo state dir for credentials.mode = "persistent"; mode 0700,
    mounted read-write into every dev container of this repo."""
    return root / ".catraz" / "state" / profile
