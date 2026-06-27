import os
import pytest
from catraz import paths, __version__


def test_asset_root_extracts(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(paths.Path, "home", lambda: tmp_path)
    root = paths.asset_root()
    assert root == tmp_path / ".cache" / "catraz" / __version__
    assert (root / "assets" / "warden").is_dir()


def test_version():
    assert __version__ == "0.2.0"


def test_find_root_walks_up(tmp_path, monkeypatch):
    (tmp_path / ".catraz").mkdir(); sub = tmp_path / "a" / "b"; sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    from catraz import paths
    assert paths.find_root() == tmp_path


def test_nested_catraz_refused(tmp_path):
    (tmp_path / ".catraz").mkdir()
    (tmp_path / "inner").mkdir(); (tmp_path / "inner" / ".catraz").mkdir()
    from catraz import paths, errors
    import pytest
    with pytest.raises(errors.CliError):
        paths.find_root(str(tmp_path))


def test_asset_cache_refreshes_on_source_change(tmp_path, monkeypatch):
    monkeypatch.setattr(paths.Path, "home", lambda: tmp_path)
    root = paths.asset_root()
    sig1 = (root / ".extracted").read_text()
    src = paths._repo_root() / "src/catraz/assets/compose/docker-compose.yml"
    orig = src.stat().st_mtime
    try:
        new_mtime = orig + 100000  # large offset so it's definitely newer than all other assets
        os.utime(src, (new_mtime, new_mtime))          # source "changed" → newer mtime
        paths.asset_root()                              # second resolution must re-extract
        sig2 = (root / ".extracted").read_text()
        assert sig2 != sig1                             # signature changed → cache rebuilt
    finally:
        os.utime(src, (orig, orig))                     # leave the working tree mtimes intact


def test_asset_cache_stable_without_change(tmp_path, monkeypatch):
    monkeypatch.setattr(paths.Path, "home", lambda: tmp_path)
    sig1 = (paths.asset_root() / ".extracted").read_text()
    sig2 = (paths.asset_root() / ".extracted").read_text()
    assert sig1 == sig2                                 # no churn when source is unchanged
