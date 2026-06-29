"""Test that _run_sync honors CLAUDE_CREDENTIAL_SOURCE from the shell env."""
from pathlib import Path
from unittest.mock import MagicMock
import pytest


def _make_root(tmp_path: Path) -> Path:
    cat = tmp_path / ".catraz"
    cat.mkdir(parents=True)
    (cat / ".env").write_text("AUTH_MODE=subscription\n")
    (cat / "claude").mkdir()
    return tmp_path


def _stub_entrypoint(tmp_path: Path) -> Path:
    """Write a stub entrypoint.py so _run_sync finds a real file."""
    assets = tmp_path / "assets" / "container"
    assets.mkdir(parents=True)
    (assets / "entrypoint.py").write_text("# stub\n")
    return tmp_path  # this is the asset_root


def test_shell_env_credential_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLAUDE_CREDENTIAL_SOURCE in os.environ → passed as --from."""
    import catraz.commands.setup as setup
    from catraz.commands.setup import _sync as setup_sync
    root = _make_root(tmp_path / "project")
    asset_root = _stub_entrypoint(tmp_path / "cache")

    captured: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        captured.append(cmd)
        r = MagicMock()
        r.returncode = 0
        return r

    monkeypatch.setattr(setup_sync.subprocess, "run", fake_run)  # type: ignore[attr-defined]
    from catraz import paths
    monkeypatch.setattr(paths, "asset_root", lambda: asset_root)
    monkeypatch.setenv("CLAUDE_CREDENTIAL_SOURCE", "/custom/claude")

    out = MagicMock()
    setup._run_sync(root, out)

    assert len(captured) == 1
    cmd = captured[0]
    assert "--from" in cmd
    idx = cmd.index("--from")
    assert cmd[idx + 1] == "/custom/claude"


def test_from_flag_overrides_shell_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--from flag takes precedence over shell env CLAUDE_CREDENTIAL_SOURCE."""
    import catraz.commands.setup as setup
    from catraz.commands.setup import _sync as setup_sync
    root = _make_root(tmp_path / "project")
    asset_root = _stub_entrypoint(tmp_path / "cache")

    captured: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> MagicMock:
        captured.append(cmd)
        r = MagicMock()
        r.returncode = 0
        return r

    monkeypatch.setattr(setup_sync.subprocess, "run", fake_run)  # type: ignore[attr-defined]
    from catraz import paths
    monkeypatch.setattr(paths, "asset_root", lambda: asset_root)
    monkeypatch.setenv("CLAUDE_CREDENTIAL_SOURCE", "/shell/claude")

    out = MagicMock()
    setup._run_sync(root, out, source="/explicit/claude")

    assert len(captured) == 1
    cmd = captured[0]
    assert "--from" in cmd
    idx = cmd.index("--from")
    assert cmd[idx + 1] == "/explicit/claude"
