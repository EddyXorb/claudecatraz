"""Tests for P8: catraz reload — recreate infra services with stale config."""

import argparse
import types
import typing
from pathlib import Path

import pytest

from catraz import compose
from catraz.commands import reload as reload_cmd
from catraz.errors import EXIT_OK
from catraz.ui import Out


def _out() -> Out:
    return Out(color=False)


def _seed(tmp_path: Path) -> None:
    cat = tmp_path / ".catraz"
    (cat / "config").mkdir(parents=True, exist_ok=True)
    (cat / ".env").write_text("AUTH_MODE=api_key\n")
    (cat / "config" / "warden.toml").write_text("# warden\n")
    (cat / "config" / "squid.conf").write_text("# squid\n")
    (cat / "config" / "allowlist.txt").write_text("example.com\n")


# ── cmd_reload ────────────────────────────────────────────────────────────────


def test_cmd_reload_up_to_date_no_up_call(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Nothing stale → EXIT_OK and no `up` compose call."""
    _seed(tmp_path)
    rows = [
        {"Service": "gitlab-warden", "Name": "c-warden"},
        {"Service": "forward-proxy", "Name": "c-proxy"},
    ]
    calls: list[list[str]] = []
    monkeypatch.setattr(compose, "prepare", lambda *a, **kw: ["docker", "compose"])
    monkeypatch.setattr(compose, "drifted_services", lambda *a, **kw: set())
    monkeypatch.setattr(reload_cmd, "compose_ps", lambda *a, **kw: rows)
    monkeypatch.setattr(compose, "run", lambda root, args, **kw: calls.append(args))
    rc = reload_cmd.cmd_reload(
        tmp_path, typing.cast(argparse.Namespace, types.SimpleNamespace(print_only=False)), _out()
    )
    assert rc == EXIT_OK
    assert not any("up" in c for c in calls)


def test_cmd_reload_stale_force_recreates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A drifted service → `up -d --force-recreate --build <service>` issued."""
    _seed(tmp_path)
    rows = [{"Service": "gitlab-warden", "Name": "c-warden"}]
    calls: list[list[str]] = []
    monkeypatch.setattr(compose, "prepare", lambda *a, **kw: ["docker", "compose"])
    monkeypatch.setattr(compose, "drifted_services", lambda *a, **kw: {"gitlab-warden"})
    monkeypatch.setattr(reload_cmd, "compose_ps", lambda *a, **kw: rows)

    def _mock_compose_run(root: Path, args: typing.Any, **kw: typing.Any) -> types.SimpleNamespace:
        calls.append(args)
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(compose, "run", _mock_compose_run)
    rc = reload_cmd.cmd_reload(
        tmp_path, typing.cast(argparse.Namespace, types.SimpleNamespace(print_only=False)), _out()
    )
    assert rc == EXIT_OK
    assert calls == [["up", "-d", "--force-recreate", "--build", "gitlab-warden"]]


def test_cmd_reload_not_running(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No running containers → EXIT_OK, no up call."""
    _seed(tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(compose, "prepare", lambda *a, **kw: ["docker", "compose"])
    monkeypatch.setattr(compose, "drifted_services", lambda *a, **kw: set())
    monkeypatch.setattr(reload_cmd, "compose_ps", lambda *a, **kw: [])
    monkeypatch.setattr(compose, "run", lambda root, args, **kw: calls.append(args))
    rc = reload_cmd.cmd_reload(
        tmp_path, typing.cast(argparse.Namespace, types.SimpleNamespace(print_only=False)), _out()
    )
    assert rc == EXIT_OK
    assert calls == []


def test_cmd_reload_force_not_running_rebuilds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--force with the stack down → rebuild + start every infra service anyway."""
    _seed(tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(compose, "prepare", lambda *a, **kw: ["docker", "compose"])
    monkeypatch.setattr(compose, "drifted_services", lambda *a, **kw: set())
    monkeypatch.setattr(reload_cmd, "compose_ps", lambda *a, **kw: [])

    def _mock_compose_run(root: Path, args: typing.Any, **kw: typing.Any) -> types.SimpleNamespace:
        calls.append(args)
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(compose, "run", _mock_compose_run)
    rc = reload_cmd.cmd_reload(
        tmp_path,
        typing.cast(argparse.Namespace, types.SimpleNamespace(print_only=False, force=True)),
        _out(),
    )
    assert rc == EXIT_OK
    assert calls == [["up", "-d", "--force-recreate", "--build", "forward-proxy", "gitlab-warden"]]


def test_cmd_reload_force_running_not_stale(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--force ignores staleness: rebuilds even when config is up to date."""
    _seed(tmp_path)
    rows = [
        {"Service": "gitlab-warden", "Name": "c-warden"},
        {"Service": "forward-proxy", "Name": "c-proxy"},
    ]
    calls: list[list[str]] = []
    monkeypatch.setattr(compose, "prepare", lambda *a, **kw: ["docker", "compose"])
    monkeypatch.setattr(compose, "drifted_services", lambda *a, **kw: set())
    monkeypatch.setattr(reload_cmd, "compose_ps", lambda *a, **kw: rows)

    def _mock_compose_run(root: Path, args: typing.Any, **kw: typing.Any) -> types.SimpleNamespace:
        calls.append(args)
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(compose, "run", _mock_compose_run)
    rc = reload_cmd.cmd_reload(
        tmp_path,
        typing.cast(argparse.Namespace, types.SimpleNamespace(print_only=False, force=True)),
        _out(),
    )
    assert rc == EXIT_OK
    assert calls == [["up", "-d", "--force-recreate", "--build", "forward-proxy", "gitlab-warden"]]
