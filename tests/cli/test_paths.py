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
