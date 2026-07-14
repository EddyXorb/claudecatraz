"""P10: non-interactive `catraz run` tees its transcript to .catraz/logs/agent."""

import argparse
import os
import shutil
import sys
import types
from pathlib import Path
from typing import Any, cast

import pytest

from catraz import image, compose as compose_mod
from catraz.commands import run as run_cmd
from catraz.ui import Out


# ── _prune_agent_logs ─────────────────────────────────────────────────────────


def test_prune_keeps_newest_50(tmp_path: Path) -> None:
    for i in range(53):
        (tmp_path / f"{i:04d}.log").write_text("x")
    run_cmd._prune_agent_logs(tmp_path, keep=50)
    remaining = sorted(p.name for p in tmp_path.glob("*.log"))
    assert len(remaining) == 50
    # the 3 lowest-sorted (oldest by fixed-width name) are gone
    assert remaining[0] == "0003.log"
    assert remaining[-1] == "0052.log"


# ── cmd_run tee wiring ────────────────────────────────────────────────────────


def _mock_cmd_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[dict[str, Any]]:
    """Stub every side-effecting collaborator cmd_run touches; record compose_run kwargs."""
    (tmp_path / ".catraz").mkdir()
    (tmp_path / ".catraz" / ".env").write_text("AUTH_MODE=api_key\n")
    compose_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(run_cmd, "assert_real_dirs", lambda *a, **k: None)
    monkeypatch.setattr(run_cmd, "assert_invariants", lambda *a, **k: None)
    monkeypatch.setattr(run_cmd, "_ensure_infra", lambda *a, **k: None)
    monkeypatch.setattr(image, "resolve_base", lambda root: "catraz-base:test")
    monkeypatch.setattr(
        compose_mod,
        "prepare",
        lambda root, *, render, extra_env=None: ["docker", "compose"],
    )

    def fake_compose_run(
        root: Path,
        args: list[str],
        *,
        prefix: list[str] | None = None,
        check: bool = True,
        tee: Path | None = None,
        **k: object,
    ) -> types.SimpleNamespace:
        compose_calls.append({"args": list(args), "tee": tee})
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(run_cmd, "compose_run", fake_compose_run)
    return compose_calls


def test_cmd_run_non_tty_tees_transcript(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    compose_calls = _mock_cmd_run(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: False))
    monkeypatch.chdir(tmp_path)
    args = cast(argparse.Namespace, types.SimpleNamespace(claude_args=[]))
    rc = run_cmd.cmd_run(tmp_path, args, Out(color=False))
    assert rc == 0
    assert len(compose_calls) == 1
    tee = compose_calls[0]["tee"]
    assert tee is not None
    assert tee.parent == tmp_path / ".catraz/logs/agent"
    assert tee.name.endswith(".log")


def test_cmd_run_tty_no_tee(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    compose_calls = _mock_cmd_run(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    monkeypatch.chdir(tmp_path)
    args = cast(argparse.Namespace, types.SimpleNamespace(claude_args=[]))
    rc = run_cmd.cmd_run(tmp_path, args, Out(color=False))
    assert rc == 0
    assert len(compose_calls) == 1
    assert compose_calls[0]["tee"] is None


# ── compose.run tee streaming ─────────────────────────────────────────────────


@pytest.mark.skipif(
    os.name != "posix" or shutil.which("bash") is None, reason="tees a POSIX shell subprocess"
)
def test_compose_run_tee_writes_file(tmp_path: Path) -> None:
    # prefix=[] is mandatory: an omitted prefix defaults to the docker-compose source cmd.
    log = tmp_path / "out.log"
    r = compose_mod.run(tmp_path, ["bash", "-c", "printf hello"], prefix=[], check=False, tee=log)
    assert r is not None
    assert r.returncode == 0
    assert log.read_text() == "hello"
