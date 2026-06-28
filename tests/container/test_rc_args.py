import pytest


def test_rc_args_defaults(ep, tmp_path, monkeypatch):
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


def test_rc_args_env_driven(ep, tmp_path, monkeypatch):
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


def test_permission_mode_always_hardcoded(ep, tmp_path, monkeypatch):
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
