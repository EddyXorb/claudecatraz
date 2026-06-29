from pathlib import Path
from typing import Any
import pytest


def test_rc_args_defaults(ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No env set → argv equals today's literal list (regression guard)."""
    calls = []
    monkeypatch.setattr(ep, "drop_to_dev", lambda: None)
    monkeypatch.setattr(ep, "build_claude_home", lambda *a, **kw: None)
    monkeypatch.setattr(ep, "configure_git_warden", lambda: None)
    monkeypatch.setattr(ep.os, "execvp", lambda prog, argv: calls.append((prog, argv)))
    monkeypatch.delenv("CLAUDE_RC_SPAWN", raising=False)
    monkeypatch.delenv("CLAUDE_RC_DEBUG_FILE", raising=False)
    monkeypatch.delenv("CLAUDE_RC_EXTRA_ARGS", raising=False)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    ep.cmd_start(tmp_path / ".claude")
    assert len(calls) == 1
    prog, argv = calls[0]
    assert prog == "claude"
    assert argv[:4] == ["claude", "remote-control", "--permission-mode", "bypassPermissions"]
    assert "--spawn" in argv and argv[argv.index("--spawn") + 1] == "same-dir"
    assert "--debug-file" in argv
    assert "--permission-mode" not in ep.os.environ if hasattr(ep.os, "environ") else True


def test_rc_args_env_driven(ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLAUDE_RC_SPAWN and CLAUDE_RC_EXTRA_ARGS override the defaults."""
    calls = []
    monkeypatch.setattr(ep, "drop_to_dev", lambda: None)
    monkeypatch.setattr(ep, "build_claude_home", lambda *a, **kw: None)
    monkeypatch.setattr(ep, "configure_git_warden", lambda: None)
    monkeypatch.setattr(ep.os, "execvp", lambda prog, argv: calls.append((prog, argv)))
    monkeypatch.setenv("CLAUDE_RC_SPAWN", "project-dir")
    monkeypatch.setenv("CLAUDE_RC_EXTRA_ARGS", "--foo bar")
    monkeypatch.delenv("CLAUDE_RC_DEBUG_FILE", raising=False)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    ep.cmd_start(tmp_path / ".claude")
    prog, argv = calls[0]
    assert argv[argv.index("--spawn") + 1] == "project-dir"
    assert "--foo" in argv and "bar" in argv


def test_permission_mode_always_hardcoded(ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--permission-mode bypassPermissions is always present and never env-driven."""
    calls = []
    monkeypatch.setattr(ep, "drop_to_dev", lambda: None)
    monkeypatch.setattr(ep, "build_claude_home", lambda *a, **kw: None)
    monkeypatch.setattr(ep, "configure_git_warden", lambda: None)
    monkeypatch.setattr(ep.os, "execvp", lambda prog, argv: calls.append((prog, argv)))
    monkeypatch.delenv("CLAUDE_RC_SPAWN", raising=False)
    monkeypatch.delenv("CLAUDE_RC_DEBUG_FILE", raising=False)
    monkeypatch.delenv("CLAUDE_RC_EXTRA_ARGS", raising=False)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    ep.cmd_start(tmp_path / ".claude")
    _, argv = calls[0]
    idx = argv.index("--permission-mode")
    assert argv[idx + 1] == "bypassPermissions"


def test_api_key_file_exported_to_env(ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ANTHROPIC_API_KEY_FILE is read, stripped, and exported to os.environ before exec."""
    key_file = tmp_path / "anthropic_api_key"
    key_file.write_text("sk-ant-testkey\n")

    calls = []
    monkeypatch.setattr(ep, "drop_to_dev", lambda: None)
    monkeypatch.setattr(ep, "build_claude_home", lambda *a, **kw: None)
    monkeypatch.setattr(ep, "configure_git_warden", lambda: None)
    monkeypatch.setattr(ep.os, "execvp", lambda prog, argv: calls.append((prog, argv)))
    monkeypatch.setenv("AUTH_MODE", "api_key")
    monkeypatch.setenv("ANTHROPIC_API_KEY_FILE", str(key_file))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_RC_SPAWN", raising=False)
    monkeypatch.delenv("CLAUDE_RC_DEBUG_FILE", raising=False)
    monkeypatch.delenv("CLAUDE_RC_EXTRA_ARGS", raising=False)

    ep.cmd_start(tmp_path / ".claude")
    assert ep.os.environ["ANTHROPIC_API_KEY"] == "sk-ant-testkey"
    assert len(calls) == 1


def test_api_key_file_wins_over_bare_env(ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When both _FILE and bare var are set, the file value wins."""
    key_file = tmp_path / "anthropic_api_key"
    key_file.write_text("sk-from-file\n")

    calls = []
    monkeypatch.setattr(ep, "drop_to_dev", lambda: None)
    monkeypatch.setattr(ep, "build_claude_home", lambda *a, **kw: None)
    monkeypatch.setattr(ep, "configure_git_warden", lambda: None)
    monkeypatch.setattr(ep.os, "execvp", lambda prog, argv: calls.append((prog, argv)))
    monkeypatch.setenv("AUTH_MODE", "api_key")
    monkeypatch.setenv("ANTHROPIC_API_KEY_FILE", str(key_file))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
    monkeypatch.delenv("CLAUDE_RC_SPAWN", raising=False)
    monkeypatch.delenv("CLAUDE_RC_DEBUG_FILE", raising=False)
    monkeypatch.delenv("CLAUDE_RC_EXTRA_ARGS", raising=False)

    ep.cmd_start(tmp_path / ".claude")
    assert ep.os.environ["ANTHROPIC_API_KEY"] == "sk-from-file"
