"""P4: catraz ps — list active agent containers (incl. hidden run --rm one-offs)."""
import argparse
import typing
import types
from pathlib import Path

import pytest

from catraz import compose
from catraz.commands import observe
from catraz.ui import Out
from catraz.errors import EXIT_OK


def _out() -> Out:
    return Out(color=False)


def _patch_ps(monkeypatch: pytest.MonkeyPatch, rows: object) -> dict[str, typing.Any]:
    """Stub compose.prepare + compose.compose_ps; return the recorded ps kwargs."""
    recorded: dict[str, typing.Any] = {}
    monkeypatch.setattr(compose, "prepare", lambda root, *, render, extra_env=None: ["c"])

    def fake_ps(root: object, *, prefix: object = None, all: bool = False) -> object:
        recorded["all"] = all
        recorded["prefix"] = prefix
        return rows

    monkeypatch.setattr(compose, "compose_ps", fake_ps)
    return recorded


def test_cmd_ps_passes_all_true(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Regression guard: cmd_ps must query with all=True or one-offs are invisible."""
    rows = [{"Service": "claude-dev-env", "Name": "agent-1", "State": "running"}]
    recorded = _patch_ps(monkeypatch, rows)
    rc = observe.cmd_ps(tmp_path, typing.cast(argparse.Namespace, types.SimpleNamespace()), _out())
    assert rc == EXIT_OK
    assert recorded["all"] is True


def test_cmd_ps_filters_to_agent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rows = [
        {"Service": "gitlab-warden", "Name": "w", "State": "running"},
        {"Service": "forward-proxy", "Name": "p", "State": "running"},
        {"Service": "claude-dev-env", "Name": "agent-1", "State": "running",
         "Status": "Up 2 minutes"},
    ]
    _patch_ps(monkeypatch, rows)
    rc = observe.cmd_ps(tmp_path, typing.cast(argparse.Namespace, types.SimpleNamespace()), _out())
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "agent-1" in out
    assert "gitlab-warden" not in out
    assert "forward-proxy" not in out


def test_cmd_ps_lists_daemon_and_oneoff(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rows = [
        {"Service": "claude-dev-env", "Name": "proj-claude-dev-env-1", "State": "running"},
        {"Service": "claude-dev-env", "Name": "proj-claude-dev-env-run-abc123",
         "State": "running"},
    ]
    _patch_ps(monkeypatch, rows)
    rc = observe.cmd_ps(tmp_path, typing.cast(argparse.Namespace, types.SimpleNamespace()), _out())
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "proj-claude-dev-env-1" in out
    assert "proj-claude-dev-env-run-abc123" in out


def test_cmd_ps_no_agents(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rows = [{"Service": "gitlab-warden", "Name": "w", "State": "running"}]
    _patch_ps(monkeypatch, rows)
    rc = observe.cmd_ps(tmp_path, typing.cast(argparse.Namespace, types.SimpleNamespace()), _out())
    assert rc == EXIT_OK
    assert "No active agent containers." in capsys.readouterr().out


# ── compose_ps -a plumbing ────────────────────────────────────────────────────

def test_compose_ps_all_adds_dash_a(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    recorded: dict[str, typing.Any] = {}

    def fake_run(root: object, args: list[str], *, prefix: object = None, capture: bool = False, check: bool = True, **k: typing.Any) -> types.SimpleNamespace:
        recorded["args"] = list(args)
        return types.SimpleNamespace(returncode=0, stdout="[]")

    monkeypatch.setattr(compose, "run", fake_run)
    compose.compose_ps(tmp_path, prefix=[], all=True)
    assert "-a" in recorded["args"]


def test_compose_ps_default_no_dash_a(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    recorded: dict[str, typing.Any] = {}

    def fake_run(root: object, args: list[str], *, prefix: object = None, capture: bool = False, check: bool = True, **k: typing.Any) -> types.SimpleNamespace:
        recorded["args"] = list(args)
        return types.SimpleNamespace(returncode=0, stdout="[]")

    monkeypatch.setattr(compose, "run", fake_run)
    compose.compose_ps(tmp_path, prefix=[])
    assert "-a" not in recorded["args"]
