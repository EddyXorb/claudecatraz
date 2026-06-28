import types
import pytest
from catraz import cli, paths
from catraz.commands import setup
from catraz.commands.setup import _sync as setup_sync
from catraz.errors import CliError


def _seed(tmp_path):
    (tmp_path / ".catraz").mkdir()
    (tmp_path / ".catraz/.env").write_text("AUTH_MODE=subscription\n")


def test_run_sync_uses_asset_entrypoint(tmp_path, monkeypatch):
    _seed(tmp_path)
    fake_assets = tmp_path / "cache"
    entry = fake_assets / "assets/container/entrypoint.py"
    entry.parent.mkdir(parents=True); entry.write_text("# tool")
    monkeypatch.setattr(paths, "asset_root", lambda: fake_assets)   # local import → this is the live symbol
    seen = {}
    monkeypatch.setattr(setup_sync.subprocess, "run",
                        lambda cmd, **k: seen.update(cmd=cmd) or types.SimpleNamespace(returncode=0))
    cli._run_sync(tmp_path, cli.Out(color=False))
    assert str(entry) in seen["cmd"]


def test_run_sync_raises_when_asset_missing(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setattr(paths, "asset_root", lambda: tmp_path / "empty")
    with pytest.raises(CliError):
        cli._run_sync(tmp_path, cli.Out(color=False))
