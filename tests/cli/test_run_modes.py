"""P2: `catraz run [claude|claude-remote|shell]` mode dispatch."""
import types

import pytest

from catraz import image, compose as compose_mod
from catraz.commands import run as run_cmd
from catraz.ui import Out


def _out():
    return Out(color=False)


def _ns(claude_args):
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
def test_mode_parse_dispatches_oneoff(monkeypatch, claude_args, exp_sub, exp_raw):
    captured = {}
    monkeypatch.setattr(run_cmd, "_run_oneoff",
                        lambda root, out, sub, raw: captured.update(sub=sub, raw=raw) or 0)
    rc = run_cmd.cmd_run("/root", _ns(claude_args), _out())
    assert rc == 0
    assert captured == {"sub": exp_sub, "raw": exp_raw}


def test_claude_remote_routes_to_daemon(monkeypatch):
    called = {}
    monkeypatch.setattr(run_cmd, "_start_remote_daemon",
                        lambda root, args, out: called.update(hit=True) or 0)
    monkeypatch.setattr(run_cmd, "_run_oneoff",
                        lambda *a, **k: pytest.fail("should not run a one-off"))
    rc = run_cmd.cmd_run("/root", _ns(["claude-remote"]), _out())
    assert rc == 0 and called == {"hit": True}


# ── claude-remote daemon port ─────────────────────────────────────────────────

def test_start_remote_daemon_brings_up_remote_profile(monkeypatch, tmp_path):
    (tmp_path / ".catraz").mkdir()
    compose_calls = []
    monkeypatch.setattr(run_cmd, "assert_real_dirs", lambda *a, **k: None)
    monkeypatch.setattr(run_cmd, "assert_invariants", lambda *a, **k: None)
    monkeypatch.setattr(run_cmd.auth, "write_auth_fragment", lambda root: None)
    monkeypatch.setattr(run_cmd, "_security_preflight", lambda root, out: False)
    monkeypatch.setattr(run_cmd, "_auto_sync_if_needed", lambda root, out: None)
    monkeypatch.setattr(run_cmd, "_wait_healthy", lambda *a, **k: None)
    monkeypatch.setattr(run_cmd, "_print_urls", lambda out: None)
    resolve_calls = []
    monkeypatch.setattr(image, "resolve_base",
                        lambda root: resolve_calls.append(root) or "catraz-base:test")
    monkeypatch.setattr(compose_mod, "prepare",
                        lambda root, *, render, extra_env=None: ["docker", "compose"])
    monkeypatch.setattr(run_cmd, "compose_run",
                        lambda root, args, **k: compose_calls.append(list(args))
                        or types.SimpleNamespace(returncode=0))
    rc = run_cmd.cmd_run(tmp_path, _ns(["claude-remote"]), _out())
    assert rc == 0
    assert compose_calls == [["--profile", "remote", "up", "-d"]]
    assert resolve_calls  # base image resolved


def test_start_remote_daemon_preflight_failure(monkeypatch, tmp_path):
    (tmp_path / ".catraz").mkdir()
    monkeypatch.setattr(run_cmd, "assert_real_dirs", lambda *a, **k: None)
    monkeypatch.setattr(run_cmd.auth, "write_auth_fragment", lambda root: None)
    monkeypatch.setattr(run_cmd, "_security_preflight", lambda root, out: True)
    monkeypatch.setattr(run_cmd, "compose_run",
                        lambda *a, **k: pytest.fail("must not start the stack on preflight fail"))
    from catraz.errors import EXIT_DOCTOR
    rc = run_cmd.cmd_run(tmp_path, _ns(["claude-remote"]), _out())
    assert rc == EXIT_DOCTOR


# ── one-off tee behavior per mode (item 03 regression) ────────────────────────

def _mock_oneoff(monkeypatch, tmp_path):
    (tmp_path / ".catraz").mkdir()
    (tmp_path / ".catraz" / ".env").write_text("AUTH_MODE=api_key\n")
    calls = []
    monkeypatch.setattr(run_cmd, "assert_real_dirs", lambda *a, **k: None)
    monkeypatch.setattr(run_cmd, "assert_invariants", lambda *a, **k: None)
    monkeypatch.setattr(run_cmd, "_ensure_infra", lambda *a, **k: None)
    monkeypatch.setattr(image, "resolve_base", lambda root: "catraz-base:test")
    monkeypatch.setattr(compose_mod, "prepare",
                        lambda root, *, render, extra_env=None: ["docker", "compose"])
    monkeypatch.setattr(run_cmd, "compose_run",
                        lambda root, args, *, prefix=None, check=True, tee=None, **k:
                        calls.append({"args": list(args), "tee": tee})
                        or types.SimpleNamespace(returncode=0))
    return calls


def test_claude_non_tty_tees(monkeypatch, tmp_path):
    calls = _mock_oneoff(monkeypatch, tmp_path)
    monkeypatch.setattr(run_cmd.sys, "stdin", types.SimpleNamespace(isatty=lambda: False))
    monkeypatch.chdir(tmp_path)
    run_cmd.cmd_run(tmp_path, _ns([]), _out())
    assert calls[0]["tee"] is not None
    assert calls[0]["tee"].parent == tmp_path / ".catraz/logs/agent"


def test_shell_never_tees(monkeypatch, tmp_path):
    calls = _mock_oneoff(monkeypatch, tmp_path)
    monkeypatch.setattr(run_cmd.sys, "stdin", types.SimpleNamespace(isatty=lambda: False))
    monkeypatch.chdir(tmp_path)
    run_cmd.cmd_run(tmp_path, _ns(["shell", "ls"]), _out())
    assert calls[0]["tee"] is None
    # shell maps to the `exec` entrypoint subcommand, not `run`
    assert "exec" in calls[0]["args"] and calls[0]["args"][-1] == "ls"
