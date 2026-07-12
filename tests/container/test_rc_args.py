"""claude adapter: `remote_command()` (remote-control argv) and `environ()`
(api_key resolution), behind the AgentAdapter contract. Uses the real
adapter staged by path via the `claude_adapter` fixture."""

from pathlib import Path
from typing import Any
import pytest


def test_remote_command_defaults(claude_adapter: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """No env set → argv equals today's literal list (regression guard)."""
    monkeypatch.delenv("CLAUDE_RC_SPAWN", raising=False)
    monkeypatch.delenv("CLAUDE_RC_DEBUG_FILE", raising=False)
    monkeypatch.delenv("CLAUDE_RC_EXTRA_ARGS", raising=False)
    monkeypatch.delenv("AGENT_LOG_DIR", raising=False)
    argv = claude_adapter.remote_command()
    assert argv is not None
    assert argv[:4] == [
        "claude",
        "remote-control",
        "--permission-mode",
        "bypassPermissions",
    ]
    assert "--spawn" in argv and argv[argv.index("--spawn") + 1] == "same-dir"
    assert "--debug-file" in argv


def test_remote_command_env_driven(claude_adapter: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLAUDE_RC_SPAWN and CLAUDE_RC_EXTRA_ARGS override the defaults."""
    monkeypatch.setenv("CLAUDE_RC_SPAWN", "project-dir")
    monkeypatch.setenv("CLAUDE_RC_EXTRA_ARGS", "--foo bar")
    monkeypatch.delenv("CLAUDE_RC_DEBUG_FILE", raising=False)
    argv = claude_adapter.remote_command()
    assert argv is not None
    assert argv[argv.index("--spawn") + 1] == "project-dir"
    assert "--foo" in argv and "bar" in argv


def test_permission_mode_always_hardcoded(
    claude_adapter: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--permission-mode bypassPermissions is always present and never env-driven."""
    monkeypatch.delenv("CLAUDE_RC_SPAWN", raising=False)
    monkeypatch.delenv("CLAUDE_RC_DEBUG_FILE", raising=False)
    monkeypatch.delenv("CLAUDE_RC_EXTRA_ARGS", raising=False)
    argv = claude_adapter.remote_command()
    assert argv is not None
    idx = argv.index("--permission-mode")
    assert argv[idx + 1] == "bypassPermissions"


def _secrets(claude_adapter: Any, **overrides: Any) -> Any:
    base = dict(
        auth_mode="api_key",
        subscription_ro_dir=None,
        api_key_file=None,
        api_key_env_fallback="",
        remote=False,
    )
    base.update(overrides)
    return claude_adapter.Secrets(**base)


def test_api_key_file_exported_to_env(claude_adapter: Any, tmp_path: Path) -> None:
    """`environ()` reads the file secret and returns it under ANTHROPIC_API_KEY."""
    key_file = tmp_path / "anthropic_api_key"
    key_file.write_text("sk-ant-testkey\n")
    env = claude_adapter.environ(_secrets(claude_adapter, api_key_file=key_file))
    assert env == {"ANTHROPIC_API_KEY": "sk-ant-testkey"}


def test_api_key_file_wins_over_bare_env(claude_adapter: Any, tmp_path: Path) -> None:
    """When both a file secret and the bare fallback are set, the file wins."""
    key_file = tmp_path / "anthropic_api_key"
    key_file.write_text("sk-from-file\n")
    env = claude_adapter.environ(
        _secrets(claude_adapter, api_key_file=key_file, api_key_env_fallback="sk-from-env")
    )
    assert env == {"ANTHROPIC_API_KEY": "sk-from-file"}


def test_api_key_falls_back_to_bare_env(claude_adapter: Any) -> None:
    env = claude_adapter.environ(_secrets(claude_adapter, api_key_env_fallback="sk-bare"))
    assert env == {"ANTHROPIC_API_KEY": "sk-bare"}


def test_api_key_missing_raises(claude_adapter: Any) -> None:
    with pytest.raises(ValueError):
        claude_adapter.environ(_secrets(claude_adapter))


def test_subscription_mode_environ_is_empty(claude_adapter: Any) -> None:
    """`environ()` never touches ANTHROPIC_API_KEY in subscription mode."""
    env = claude_adapter.environ(_secrets(claude_adapter, auth_mode="subscription"))
    assert env == {}
