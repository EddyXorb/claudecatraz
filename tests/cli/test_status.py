"""Tests for B3: cmd_status exit-code reflects stack health."""
import types
import pytest
from catraz.commands import stack
from catraz.ui import Out
from catraz.errors import EXIT_OK, EXIT_GENERAL


def _out():
    return Out(color=False)


def _seed(tmp_path):
    (tmp_path / ".catraz").mkdir(exist_ok=True)
    (tmp_path / ".catraz" / ".env").write_text("AUTH_MODE=api_key\n")


def test_status_not_setup_returns_ok(tmp_path):
    """No .env → EXIT_OK (not an error, just 'nothing set up yet')."""
    (tmp_path / ".catraz").mkdir(exist_ok=True)
    args = types.SimpleNamespace()
    rc = stack.cmd_status(tmp_path, args, _out())
    assert rc == EXIT_OK


def test_status_not_running_returns_general(monkeypatch, tmp_path):
    """Set up but compose_ps returns [] → EXIT_GENERAL."""
    _seed(tmp_path)
    monkeypatch.setattr(stack, "compose_ps", lambda root: [])
    rc = stack.cmd_status(tmp_path, types.SimpleNamespace(), _out())
    assert rc == EXIT_GENERAL


def test_status_all_ready_returns_ok(monkeypatch, tmp_path):
    """All services running+healthy → EXIT_OK."""
    _seed(tmp_path)
    rows = [
        {"Service": "gitlab-warden", "State": "running", "Health": "healthy"},
        {"Service": "forward-proxy", "State": "running", "Health": "healthy"},
    ]
    monkeypatch.setattr(stack, "compose_ps", lambda root: rows)
    monkeypatch.setattr(stack, "_print_urls", lambda out: None)
    rc = stack.cmd_status(tmp_path, types.SimpleNamespace(), _out())
    assert rc == EXIT_OK


def test_status_partial_ready_returns_general(monkeypatch, tmp_path):
    """At least one service not ready → EXIT_GENERAL."""
    _seed(tmp_path)
    rows = [
        {"Service": "gitlab-warden", "State": "running", "Health": "healthy"},
        {"Service": "forward-proxy", "State": "starting", "Health": ""},
    ]
    monkeypatch.setattr(stack, "compose_ps", lambda root: rows)
    monkeypatch.setattr(stack, "_print_urls", lambda out: None)
    rc = stack.cmd_status(tmp_path, types.SimpleNamespace(), _out())
    assert rc == EXIT_GENERAL
