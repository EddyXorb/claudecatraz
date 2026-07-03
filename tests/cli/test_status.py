"""Tests for B3: cmd_status exit-code reflects stack health."""
import argparse
import types
from pathlib import Path
from typing import cast
import pytest
from catraz.commands import stack
from catraz.ui import Out
from catraz.errors import EXIT_OK, EXIT_GENERAL


def _out() -> Out:
    return Out(color=False)


def _seed(tmp_path: Path) -> None:
    (tmp_path / ".catraz").mkdir(exist_ok=True)
    (tmp_path / ".catraz" / ".env").write_text("AUTH_MODE=api_key\n")


def test_status_not_setup_returns_ok(tmp_path: Path) -> None:
    """No .env → EXIT_OK (not an error, just 'nothing set up yet')."""
    (tmp_path / ".catraz").mkdir(exist_ok=True)
    args = cast(argparse.Namespace, types.SimpleNamespace())
    rc = stack.cmd_status(tmp_path, args, _out())
    assert rc == EXIT_OK


def test_status_not_running_returns_general(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Set up but compose_ps returns [] → EXIT_GENERAL."""
    _seed(tmp_path)
    monkeypatch.setattr(stack, "compose_ps", lambda *a, **kw: [])
    rc = stack.cmd_status(tmp_path, cast(argparse.Namespace, types.SimpleNamespace()), _out())
    assert rc == EXIT_GENERAL


def test_status_all_ready_returns_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """All services running+healthy → EXIT_OK."""
    _seed(tmp_path)
    rows = [
        {"Service": "gitlab-warden", "State": "running", "Health": "healthy"},
        {"Service": "forward-proxy", "State": "running", "Health": "healthy"},
    ]
    monkeypatch.setattr(stack, "compose_ps", lambda *a, **kw: rows)
    monkeypatch.setattr(stack, "_print_urls", lambda out: None)
    rc = stack.cmd_status(tmp_path, cast(argparse.Namespace, types.SimpleNamespace()), _out())
    assert rc == EXIT_OK


def test_status_partial_ready_returns_general(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """At least one service not ready → EXIT_GENERAL."""
    _seed(tmp_path)
    rows = [
        {"Service": "gitlab-warden", "State": "running", "Health": "healthy"},
        {"Service": "forward-proxy", "State": "starting", "Health": ""},
    ]
    monkeypatch.setattr(stack, "compose_ps", lambda *a, **kw: rows)
    monkeypatch.setattr(stack, "_print_urls", lambda out: None)
    rc = stack.cmd_status(tmp_path, cast(argparse.Namespace, types.SimpleNamespace()), _out())
    assert rc == EXIT_GENERAL
