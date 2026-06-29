import os
from pathlib import Path
import pytest
from catraz import paths, __version__


def test_asset_root_extracts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CATRAZ_CACHE_DIR", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    root = paths.asset_root()
    assert root == tmp_path / ".cache" / "catraz" / __version__
    assert (root / "assets" / "warden").is_dir()


def test_version() -> None:
    import tomllib
    root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text())
    assert __version__ == pyproject["project"]["version"]


def test_find_root_walks_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".catraz").mkdir(); sub = tmp_path / "a" / "b"; sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    from catraz import paths
    assert paths.find_root() == tmp_path


def test_nested_catraz_refused(tmp_path: Path) -> None:
    (tmp_path / ".catraz").mkdir()
    (tmp_path / "inner").mkdir(); (tmp_path / "inner" / ".catraz").mkdir()
    from catraz import paths, errors
    with pytest.raises(errors.CliError):
        paths.find_root(str(tmp_path))


def test_asset_cache_refreshes_on_source_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CATRAZ_CACHE_DIR", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    root = paths.asset_root()
    sig1 = (root / ".extracted").read_text()
    src_root = paths._repo_root()
    assert src_root is not None
    src = src_root / "src/catraz/assets/compose/docker-compose.yml"
    orig = src.stat().st_mtime
    try:
        new_mtime = orig + 100000  # large offset so it's definitely newer than all other assets
        os.utime(src, (new_mtime, new_mtime))          # source "changed" → newer mtime
        paths.asset_root()                              # second resolution must re-extract
        sig2 = (root / ".extracted").read_text()
        assert sig2 != sig1                             # signature changed → cache rebuilt
    finally:
        os.utime(src, (orig, orig))                     # leave the working tree mtimes intact


def test_asset_cache_stable_without_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CATRAZ_CACHE_DIR", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    sig1 = (paths.asset_root() / ".extracted").read_text()
    sig2 = (paths.asset_root() / ".extracted").read_text()
    assert sig1 == sig2                                 # no churn when source is unchanged


def test_catraz_cache_dir_overrides_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CATRAZ_CACHE_DIR → <dir>/catraz/<v>."""
    cache_dir = tmp_path / "custom_cache"
    cache_dir.mkdir()
    monkeypatch.setenv("CATRAZ_CACHE_DIR", str(cache_dir))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    root = paths.asset_root()
    assert root == cache_dir / "catraz" / __version__


def test_xdg_cache_home_overrides_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """XDG_CACHE_HOME set (no CATRAZ_CACHE_DIR) → <xdg>/catraz/<v>."""
    xdg_dir = tmp_path / "xdg_cache"
    xdg_dir.mkdir()
    monkeypatch.delenv("CATRAZ_CACHE_DIR", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_dir))
    root = paths.asset_root()
    assert root == xdg_dir / "catraz" / __version__


def test_claude_home_is_under_secrets(tmp_path: Path) -> None:
    """claude_home must live under secrets/ (Workstream C)."""
    ch = paths.claude_home(tmp_path)
    assert ch == tmp_path / ".catraz" / "secrets" / "claude"
