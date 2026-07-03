"""Test that _run_sync honors CLAUDE_CREDENTIAL_SOURCE from the shell env —
now passed straight to the adapter's `sync_from_host(source, home)` in-process
(§05.2/§05.3), no subprocess indirection."""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
import pytest


def _make_root(tmp_path: Path) -> Path:
    cat = tmp_path / ".catraz"
    cat.mkdir(parents=True)
    (cat / ".env").write_text("AUTH_MODE=subscription\n")
    (cat / "claude").mkdir()
    return tmp_path


class _RecordingAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, Any]] = []

    def sync_from_host(self, source: Any, home: Any) -> None:
        self.calls.append((source, home))


def test_shell_env_credential_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLAUDE_CREDENTIAL_SOURCE in os.environ → passed as `source` to sync_from_host."""
    import catraz.commands.setup as setup
    from catraz.commands.setup import _sync as setup_sync

    monkeypatch.setattr(setup_sync, "_credentials_mode", lambda root: "sync")
    root = _make_root(tmp_path / "project")
    adapter = _RecordingAdapter()
    monkeypatch.setattr(setup_sync, "load_adapter_module", lambda profile: adapter)
    monkeypatch.setenv("CLAUDE_CREDENTIAL_SOURCE", "/custom/claude")

    out = MagicMock()
    setup._run_sync(root, out)

    assert len(adapter.calls) == 1
    source, _home = adapter.calls[0]
    assert str(source) == "/custom/claude"


def test_from_flag_overrides_shell_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--from flag takes precedence over shell env CLAUDE_CREDENTIAL_SOURCE."""
    import catraz.commands.setup as setup
    from catraz.commands.setup import _sync as setup_sync

    monkeypatch.setattr(setup_sync, "_credentials_mode", lambda root: "sync")
    root = _make_root(tmp_path / "project")
    adapter = _RecordingAdapter()
    monkeypatch.setattr(setup_sync, "load_adapter_module", lambda profile: adapter)
    monkeypatch.setenv("CLAUDE_CREDENTIAL_SOURCE", "/shell/claude")

    out = MagicMock()
    setup._run_sync(root, out, source="/explicit/claude")

    assert len(adapter.calls) == 1
    source, _home = adapter.calls[0]
    assert str(source) == "/explicit/claude"
