import pytest
from catraz import paths, __version__


@pytest.mark.xfail(reason="assets in 1.3")
def test_asset_root_extracts(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(paths.Path, "home", lambda: tmp_path)
    root = paths.asset_root()
    assert root == tmp_path / ".cache" / "catraz" / __version__
    assert (root / "assets" / "warden").is_dir()
    assert (root / "assets" / "compose" / "docker-compose.yml").exists()


def test_version():
    assert __version__ == "0.2.0"
