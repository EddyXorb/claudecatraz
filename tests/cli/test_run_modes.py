"""P2: `catraz run [claude|claude-remote|shell]` mode dispatch."""
import argparse
import sys
import typing
import types
from pathlib import Path

import pytest

from catraz import image, compose as compose_mod, auth
from catraz.commands import run as run_cmd
from catraz.ui import Out


def _out() -> Out:
    return Out(color=False)


def _ns(claude_args: object) -> types.SimpleNamespace:
    return types.SimpleNamespace(claude_args=claude_args)


# ── mode parse + dispatch ─────────────────────────────────────────────────────

@pytest.mark.parametrize("claude_args, exp_sub, exp_raw", [
    ([], "run", []),
    (["shell", "ls", "-la"], "exec", ["ls", "-la"]),
    (["-p", "x"], "run", ["-p", "x"]),
    (["--", "-p", "x"], "run", ["-p", "x"]),
    (["claude", "--", "shell"], "run", ["shell"]),
    (["claude"], "run", []),
])
def test_mode_parse_dispatches_oneoff(monkeypatch: pytest.MonkeyPatch, claude_args: object, exp_sub: str, exp_raw: object) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(run_cmd, "_run_oneoff",
                        lambda root, out, sub, raw: captured.update(sub=sub, raw=raw) or 0)
    rc = run_cmd.cmd_run(Path("/root"), typing.cast(argparse.Namespace, _ns(claude_args)), _out())
    assert rc == 0
    assert captured == {"sub": exp_sub, "raw": exp_raw}


def test_claude_remote_routes_to_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, bool] = {}
    monkeypatch.setattr(run_cmd, "_start_remote_daemon",
                        lambda root, args, out: called.update(hit=True) or 0)
    monkeypatch.setattr(run_cmd, "_run_oneoff",
                        lambda *a, **k: pytest.fail("should not run a one-off"))
    rc = run_cmd.cmd_run(Path("/root"), typing.cast(argparse.Namespace, _ns(["claude-remote"])), _out())
    assert rc == 0 and called == {"hit": True}


# ── claude-remote daemon port ─────────────────────────────────────────────────

def test_start_remote_daemon_brings_up_remote_profile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".catraz").mkdir()
    compose_calls: list[list[str]] = []
    monkeypatch.setattr(run_cmd, "assert_real_dirs", lambda *a, **k: None)
    monkeypatch.setattr(run_cmd, "assert_invariants", lambda *a, **k: None)
    monkeypatch.setattr(run_cmd, "_security_preflight", lambda root, out: False)
    monkeypatch.setattr(run_cmd, "_auto_sync_if_needed", lambda root, out: None)
    monkeypatch.setattr(run_cmd, "_wait_healthy", lambda *a, **k: None)
    monkeypatch.setattr(run_cmd, "_print_urls", lambda out: None)
    resolve_calls: list[object] = []

    def _mock_resolve(root: object) -> str:
        resolve_calls.append(root)
        return "catraz-base:test"

    monkeypatch.setattr(image, "resolve_base", _mock_resolve)
    monkeypatch.setattr(compose_mod, "prepare",
                        lambda root, *, render, extra_env=None: ["docker", "compose"])

    def _mock_compose_run(root: Path, args: typing.Any, **k: typing.Any) -> types.SimpleNamespace:
        compose_calls.append(list(args))
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(run_cmd, "compose_run", _mock_compose_run)
    rc = run_cmd.cmd_run(tmp_path, typing.cast(argparse.Namespace, _ns(["claude-remote"])), _out())
    assert rc == 0
    assert compose_calls == [["--profile", "remote", "up", "-d"]]
    assert resolve_calls  # base image resolved


def test_start_remote_daemon_preflight_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".catraz").mkdir()
    monkeypatch.setattr(run_cmd, "assert_real_dirs", lambda *a, **k: None)
    monkeypatch.setattr(run_cmd, "_security_preflight", lambda root, out: True)
    monkeypatch.setattr(run_cmd, "compose_run",
                        lambda *a, **k: pytest.fail("must not start the stack on preflight fail"))
    from catraz.errors import EXIT_DOCTOR
    rc = run_cmd.cmd_run(tmp_path, typing.cast(argparse.Namespace, _ns(["claude-remote"])), _out())
    assert rc == EXIT_DOCTOR


# ── one-off tee behavior per mode (item 03 regression) ────────────────────────

def _mock_oneoff(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[dict[str, typing.Any]]:
    (tmp_path / ".catraz").mkdir()
    (tmp_path / ".catraz" / ".env").write_text("AUTH_MODE=api_key\n")
    calls: list[dict[str, typing.Any]] = []
    monkeypatch.setattr(run_cmd, "assert_real_dirs", lambda *a, **k: None)
    monkeypatch.setattr(run_cmd, "assert_invariants", lambda *a, **k: None)
    monkeypatch.setattr(run_cmd, "_ensure_infra", lambda *a, **k: None)
    monkeypatch.setattr(image, "resolve_base", lambda root: "catraz-base:test")
    monkeypatch.setattr(compose_mod, "prepare",
                        lambda root, *, render, extra_env=None: ["docker", "compose"])

    def _mock_compose_run_oneoff(
        root: Path, args: typing.Any, *, prefix: typing.Any = None, check: bool = True,
        tee: typing.Any = None, **k: typing.Any
    ) -> types.SimpleNamespace:
        calls.append({"args": list(args), "tee": tee})
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(run_cmd, "compose_run", _mock_compose_run_oneoff)
    return calls


def test_claude_non_tty_tees(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = _mock_oneoff(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: False))
    monkeypatch.chdir(tmp_path)
    run_cmd.cmd_run(tmp_path, typing.cast(argparse.Namespace, _ns([])), _out())
    assert calls[0]["tee"] is not None
    assert calls[0]["tee"].parent == tmp_path / ".catraz/logs/agent"


def test_shell_never_tees(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = _mock_oneoff(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: False))
    monkeypatch.chdir(tmp_path)
    run_cmd.cmd_run(tmp_path, typing.cast(argparse.Namespace, _ns(["shell", "ls"])), _out())
    assert calls[0]["tee"] is None
    # shell maps to the `exec` entrypoint subcommand, not `run`
    assert "exec" in calls[0]["args"] and calls[0]["args"][-1] == "ls"
