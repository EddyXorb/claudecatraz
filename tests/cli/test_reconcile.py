"""Background reconcile: the file-only trigger, the lock, and `reload --auto`."""

from __future__ import annotations

import argparse
import os
import time
import types
import typing
from pathlib import Path

import pytest

from catraz import background, cli, compose, live_config, reconcile
from catraz.commands import reload as reload_cmd
from catraz.errors import EXIT_GENERAL, EXIT_OK
from catraz.ui import Out

WARDEN = "gitlab-warden"


def _seed(root: Path, *, up: bool = True) -> None:
    cat = root / ".catraz"
    for d in ("config", "secrets", "state/warden/run", "logs/squid"):
        (cat / d).mkdir(parents=True, exist_ok=True)
    (cat / ".env").write_text("AUTH_MODE=api_key\n")
    (cat / "config" / "warden.toml").write_text("[git.rules]\n")
    if up:
        (cat / "state/warden/run/admin.sock").touch()


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []
    monkeypatch.setattr(background, "spawn", lambda root, args, log: calls.append(args))
    return calls


def _out() -> Out:
    return Out(color=False)


# ── trigger ───────────────────────────────────────────────────────────────────


def test_first_trigger_spawns_and_an_unchanged_one_does_not(
    tmp_path: Path, spawned: list[list[str]]
) -> None:
    _seed(tmp_path)
    reconcile.trigger(tmp_path, _out())
    reconcile.trigger(tmp_path, _out())
    assert spawned == [["reload", "--auto"]]


@pytest.mark.parametrize(
    "change",
    [
        lambda c: (c / ".env").write_text("AUTH_MODE=api_key\nSQUID_PORT=3129\n"),
        lambda c: (c / "compose.override.yml").write_text("services: {}\n"),
        lambda c: (c / "config/warden.toml").write_text('[[git.endpoint]]\nhost = "a.example"\n'),
    ],
)
def test_a_render_input_change_spawns_again(
    tmp_path: Path, spawned: list[list[str]], change: typing.Callable[[Path], object]
) -> None:
    _seed(tmp_path)
    reconcile.trigger(tmp_path, _out())
    change(tmp_path / ".catraz")
    reconcile.trigger(tmp_path, _out())
    assert len(spawned) == 2


def test_a_policy_edit_alone_spawns_nothing(tmp_path: Path, spawned: list[list[str]]) -> None:
    """Supervisors apply policy live; only render inputs warrant a recreate check."""
    _seed(tmp_path)
    reconcile.trigger(tmp_path, _out())
    (tmp_path / ".catraz/config/warden.toml").write_text("[git.rules]\nmax_open_mrs = 3\n")
    reconcile.trigger(tmp_path, _out())
    assert len(spawned) == 1


def test_stack_down_spawns_nothing(tmp_path: Path, spawned: list[list[str]]) -> None:
    _seed(tmp_path, up=False)
    reconcile.trigger(tmp_path, _out())
    assert spawned == []


def test_running_reconcile_is_not_doubled(tmp_path: Path, spawned: list[list[str]]) -> None:
    _seed(tmp_path)
    assert background.acquire(reconcile.lock_path(tmp_path), reconcile.LOCK_STALE_S)
    reconcile.trigger(tmp_path, _out())
    assert spawned == []


@pytest.mark.parametrize(
    ("stamp_age", "respawn"), [(0, False), (reconcile.RETRY_AFTER_S + 5, True)]
)
def test_out_of_sync_respawns_but_throttled(
    tmp_path: Path,
    spawned: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
    stamp_age: float,
    respawn: bool,
) -> None:
    _seed(tmp_path)
    reconcile.trigger(tmp_path, _out())
    then = time.time() - stamp_age
    os.utime(tmp_path / ".catraz/state/reconcile.stamp", (then, then))
    monkeypatch.setattr(
        live_config,
        "live_state",
        lambda root, s: live_config.LiveState(s, live_config.OUT_OF_SYNC),
    )
    reconcile.trigger(tmp_path, _out())
    assert len(spawned) == 1 + respawn


def test_notice_is_shown_once(
    tmp_path: Path, spawned: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(tmp_path, up=False)
    reconcile.notice_path(tmp_path).write_text("recreated gitlab-warden\n")
    reconcile.trigger(tmp_path, _out())
    reconcile.trigger(tmp_path, _out())
    assert capsys.readouterr().out.count("reconcile: recreated gitlab-warden") == 1


def test_trigger_costs_well_under_the_budget(tmp_path: Path, spawned: list[list[str]]) -> None:
    """The foreground share must stay far below 200 ms; this bounds the file work."""
    _seed(tmp_path)
    reconcile.trigger(tmp_path, _out())  # warm the asset cache
    t = time.perf_counter()
    for _ in range(10):
        reconcile.trigger(tmp_path, _out())
    assert (time.perf_counter() - t) / 10 < 0.05


# ── reload --auto and the lock ────────────────────────────────────────────────


def _ns(**kw: object) -> argparse.Namespace:
    return typing.cast(argparse.Namespace, types.SimpleNamespace(print_only=False, **kw))


def _fake_stack(
    monkeypatch: pytest.MonkeyPatch, drifted: set[str], up_rc: int = 0
) -> list[list[str]]:
    calls: list[list[str]] = []
    monkeypatch.setattr(compose, "prepare", lambda *a, **kw: ["docker", "compose"])
    monkeypatch.setattr(
        reload_cmd, "compose_ps", lambda *a, **kw: [{"Service": WARDEN, "Name": "w"}]
    )
    monkeypatch.setattr(compose, "drifted_services", lambda *a, **kw: drifted)
    monkeypatch.setattr(compose, "agent_routing_stale", lambda *a: False)

    def run(root: Path, args: list[str], **kw: object) -> types.SimpleNamespace:
        calls.append(args)
        return types.SimpleNamespace(returncode=up_rc)

    monkeypatch.setattr(compose, "run", run)
    return calls


@pytest.mark.parametrize(("up_rc", "note"), [(0, "recreated gitlab-warden"), (1, "failed")])
def test_auto_reload_leaves_a_notice_and_releases_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, up_rc: int, note: str
) -> None:
    _seed(tmp_path)
    _fake_stack(monkeypatch, {WARDEN}, up_rc)
    reload_cmd.cmd_reload(tmp_path, _ns(auto=True), _out())
    assert note in reconcile.notice_path(tmp_path).read_text()
    assert not reconcile.lock_path(tmp_path).exists()


def test_auto_reload_up_to_date_leaves_no_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path)
    calls = _fake_stack(monkeypatch, set())
    assert reload_cmd.cmd_reload(tmp_path, _ns(auto=True), _out()) == EXIT_OK
    assert calls == [] and not reconcile.notice_path(tmp_path).exists()


def test_auto_reload_yields_to_a_held_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(tmp_path)
    calls = _fake_stack(monkeypatch, {WARDEN})
    assert background.acquire(reconcile.lock_path(tmp_path), reconcile.LOCK_STALE_S)
    assert reload_cmd.cmd_reload(tmp_path, _ns(auto=True), _out()) == EXIT_OK
    assert calls == []


def test_manual_reload_fails_loud_when_the_lock_stays_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(tmp_path)
    calls = _fake_stack(monkeypatch, {WARDEN})
    monkeypatch.setattr(background, "wait_acquire", lambda *a, **kw: False)
    assert reload_cmd.cmd_reload(tmp_path, _ns(), _out()) == EXIT_GENERAL
    assert calls == [] and "infra lock" in capsys.readouterr().err


def test_wait_acquire_gives_up_after_timeout(tmp_path: Path) -> None:
    lock = tmp_path / "x.lock"
    assert background.acquire(lock, 60)
    t = time.monotonic()
    assert not background.wait_acquire(lock, 60, timeout_s=0.3)
    assert time.monotonic() - t < 2


# ── cli wiring ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("argv", "triggers"), [(["status"], True), (["doctor"], False), (["stop"], False)]
)
def test_cli_triggers_reconcile_outside_infra_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str], triggers: bool
) -> None:
    _seed(tmp_path)
    seen: list[Path] = []
    monkeypatch.setattr(reconcile, "trigger", lambda root, out: seen.append(root))
    monkeypatch.setitem(cli.HANDLERS, argv[0], lambda root, args, out: EXIT_OK)
    assert cli.main(["-C", str(tmp_path), *argv]) == EXIT_OK
    assert bool(seen) is triggers


def test_cli_survives_a_failing_trigger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(tmp_path)

    def boom(root: Path, out: Out) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(reconcile, "trigger", boom)
    monkeypatch.setitem(cli.HANDLERS, "status", lambda root, args, out: EXIT_OK)
    assert cli.main(["-C", str(tmp_path), "status"]) == EXIT_OK


def test_stop_waits_for_the_lock_so_a_reconcile_cannot_resurrect_the_stack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from catraz.commands import stack

    _seed(tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(compose, "prepare", lambda *a, **kw: ["docker", "compose"])
    monkeypatch.setattr(stack, "compose_run", lambda root, args, **kw: calls.append(args))
    monkeypatch.setattr(background, "wait_acquire", lambda *a, **kw: False)
    ns = typing.cast(argparse.Namespace, types.SimpleNamespace(print_only=False, volumes=False))
    assert stack.cmd_down(tmp_path, ns, _out()) == EXIT_GENERAL
    assert calls == []
