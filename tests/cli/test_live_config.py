"""live_config: host-side view of what the infra supervisors enforce."""

from __future__ import annotations

import argparse
import datetime
import os
import socket
import time
import types
import typing
from pathlib import Path

import pytest

from catraz import compose, live_config
from catraz.commands import reload as reload_cmd
from catraz.commands import run as run_cmd
from catraz.commands import setup, stack
from catraz.errors import EXIT_GENERAL, EXIT_OK
from catraz.ui import Out

UTC = datetime.timezone.utc
# Shared with the warden supervisor's test: files b"a", b"b", missing.
KNOWN_VECTOR = "833d05d3c2e7c8c503cea30a28bbf06de844a3176923399f5424cf222fbbfbd8"
WARDEN = "gitlab-warden"
PROXY = "forward-proxy"


def _seed(root: Path) -> None:
    cat = root / ".catraz"
    for d in ("config", "secrets", "state/warden/run", "logs/squid"):
        (cat / d).mkdir(parents=True, exist_ok=True)
    (cat / ".env").write_text("AUTH_MODE=api_key\n")
    (cat / "config" / "warden.toml").write_text("[git.rules]\n")
    (cat / "config" / "squid.conf").write_text("http_port 3128\n")
    (cat / "config" / "allowlist.txt").write_text(".example.com\n")
    (cat / "secrets" / "read_tokens").write_text("example.com tok\n")
    (cat / "secrets" / "write_tokens").write_text("")


def _write_status(
    root: Path, service: str, state: str, fp: str | None = None, detail: str = ""
) -> None:
    fp = fp if fp is not None else live_config.fingerprint(live_config.input_paths(root, service))
    path = root / ".catraz" / live_config.STATUS_FILES[service]
    path.write_text(f"state={state}\nfingerprint={fp}\ndetail={detail}\n")


def _age(root: Path, service: str, seconds: float) -> None:
    then = time.time() - seconds
    for p in live_config.input_paths(root, service):
        if p.exists():
            os.utime(p, (then, then))


def _out() -> Out:
    return Out(color=False)


# ── fingerprint / status parsing ──────────────────────────────────────────────


def test_fingerprint_known_vector(tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"a")
    (tmp_path / "b").write_bytes(b"b")
    assert live_config.fingerprint([tmp_path / "a", tmp_path / "b", tmp_path / "x"]) == (
        KNOWN_VECTOR
    )


def test_input_order_matches_supervisors() -> None:
    assert live_config.INPUTS[WARDEN] == (
        "config/warden.toml",
        "secrets/read_tokens",
        "secrets/write_tokens",
    )
    assert live_config.INPUTS[PROXY] == ("config/squid.conf", "config/allowlist.txt")


@pytest.mark.parametrize("text", ["", "state=applied\n", "garbage\n"])
def test_read_status_incomplete_is_none(tmp_path: Path, text: str) -> None:
    p = tmp_path / "s"
    p.write_text(text)
    assert live_config.read_status(p) is None


def test_read_status_missing_is_none(tmp_path: Path) -> None:
    assert live_config.read_status(tmp_path / "nope") is None


# ── live_state ────────────────────────────────────────────────────────────────


def test_no_status_is_unknown(tmp_path: Path) -> None:
    _seed(tmp_path)
    assert live_config.live_state(tmp_path, WARDEN).state == live_config.UNKNOWN


def test_matching_applied(tmp_path: Path) -> None:
    _seed(tmp_path)
    _write_status(tmp_path, WARDEN, "applied")
    assert live_config.live_state(tmp_path, WARDEN).state == live_config.APPLIED


def test_matching_rejected_carries_detail(tmp_path: Path) -> None:
    _seed(tmp_path)
    _write_status(tmp_path, PROXY, "rejected", detail="FATAL: bad line")
    ls = live_config.live_state(tmp_path, PROXY)
    assert (ls.state, ls.detail) == (live_config.REJECTED, "FATAL: bad line")


def test_fresh_edit_is_pending(tmp_path: Path) -> None:
    _seed(tmp_path)
    _write_status(tmp_path, WARDEN, "applied")
    (tmp_path / ".catraz/secrets/read_tokens").write_text("example.com new\n")
    assert live_config.live_state(tmp_path, WARDEN).state == live_config.PENDING


def test_old_mismatch_is_out_of_sync(tmp_path: Path) -> None:
    _seed(tmp_path)
    _write_status(tmp_path, WARDEN, "applied", fp="0" * 64)
    _age(tmp_path, WARDEN, 60)
    assert live_config.live_state(tmp_path, WARDEN).state == live_config.OUT_OF_SYNC


def test_report_flags_rejected_and_out_of_sync(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(tmp_path)
    _write_status(tmp_path, WARDEN, "rejected", detail="branch_prefixes must be non-empty")
    _write_status(tmp_path, PROXY, "applied", fp="0" * 64)
    _age(tmp_path, PROXY, 60)
    assert live_config.report(tmp_path, _out()) is True
    err = capsys.readouterr().err
    assert "gitlab-warden: config REJECTED" in err
    assert "branch_prefixes must be non-empty" in err
    assert "forward-proxy: running config differs" in err


def test_report_quiet_when_applied(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _seed(tmp_path)
    _write_status(tmp_path, WARDEN, "applied")
    _write_status(tmp_path, PROXY, "applied")
    assert live_config.report(tmp_path, _out()) is False
    assert capsys.readouterr().err == ""


def test_wait_settled_returns_once_applied(tmp_path: Path) -> None:
    _seed(tmp_path)
    _write_status(tmp_path, WARDEN, "applied", fp="0" * 64)
    sleeps: list[float] = []

    def sleep(s: float) -> None:
        sleeps.append(s)
        _write_status(tmp_path, WARDEN, "applied")

    ls = live_config.wait_settled(tmp_path, WARDEN, sleep=sleep)
    assert ls.state == live_config.APPLIED and len(sleeps) == 1


def test_wait_settled_times_out_pending(tmp_path: Path) -> None:
    _seed(tmp_path)
    _write_status(tmp_path, WARDEN, "applied", fp="0" * 64)
    clock = [time.time()]

    def sleep(s: float) -> None:
        clock[0] += 1.0

    ls = live_config.wait_settled(tmp_path, WARDEN, timeout=3, sleep=sleep, now=lambda: clock[0])
    assert ls.state == live_config.PENDING


def test_warden_running_false_without_socket(tmp_path: Path) -> None:
    _seed(tmp_path)
    assert live_config.warden_running(tmp_path) is False


def test_warden_running_true_with_listener(tmp_path: Path) -> None:
    _seed(tmp_path)
    run_dir = tmp_path / ".catraz/state/warden/run"
    if len(str(run_dir / "admin.sock")) > 100:
        pytest.skip("AF_UNIX path too long on this tmp dir")
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(run_dir / "admin.sock"))
    srv.listen(1)
    try:
        assert live_config.warden_running(tmp_path) is True
    finally:
        srv.close()


# ── compose: endpoint routing ─────────────────────────────────────────────────


def _endpoints(root: Path, *hosts: str) -> None:
    body = "".join(f'[[git.endpoint]]\nhost = "{h}"\ntype = "gitlab"\n' for h in hosts)
    (root / ".catraz/config/warden.toml").write_text(body)


def test_routed_hosts_parses_fragment(tmp_path: Path) -> None:
    _seed(tmp_path)
    (tmp_path / ".catraz/compose.hosts.yml").write_text(
        compose.render_hosts_fragment(["a.example", "b.example"], {"a.example": "1.2.3.4"})
    )
    assert compose.routed_hosts(tmp_path) == {"a.example", "b.example"}


def test_routed_hosts_empty_and_missing(tmp_path: Path) -> None:
    _seed(tmp_path)
    assert compose.routed_hosts(tmp_path) is None
    (tmp_path / ".catraz/compose.hosts.yml").write_text(compose.render_hosts_fragment([]))
    assert compose.routed_hosts(tmp_path) == set()


def test_endpoint_routing_changed(tmp_path: Path) -> None:
    _seed(tmp_path)
    assert compose.endpoint_routing_changed(tmp_path) is False  # nothing rendered yet
    _endpoints(tmp_path, "a.example")
    (tmp_path / ".catraz/compose.hosts.yml").write_text(
        compose.render_hosts_fragment(["a.example"])
    )
    assert compose.endpoint_routing_changed(tmp_path) is False
    _endpoints(tmp_path, "a.example", "b.example")
    assert compose.endpoint_routing_changed(tmp_path) is True


# ── reload staleness ──────────────────────────────────────────────────────────

LONG_AGO = datetime.datetime(2000, 1, 1, tzinfo=UTC)


def test_applied_live_is_not_stale_despite_newer_mtime(tmp_path: Path) -> None:
    _seed(tmp_path)
    _write_status(tmp_path, WARDEN, "applied")
    _write_status(tmp_path, PROXY, "applied")
    _age(tmp_path, WARDEN, 0)
    os.utime(tmp_path / ".catraz/.env", (0, 0))
    assert reload_cmd.stale_services(tmp_path, {WARDEN: LONG_AGO, PROXY: LONG_AGO}) == {}


def test_rejected_is_not_stale(tmp_path: Path) -> None:
    """A recreate would hit the same invalid file; reload reports, not rebuilds."""
    _seed(tmp_path)
    _write_status(tmp_path, WARDEN, "rejected", detail="x")
    os.utime(tmp_path / ".catraz/.env", (0, 0))
    assert reload_cmd.stale_services(tmp_path, {WARDEN: LONG_AGO}) == {}


def test_out_of_sync_is_stale(tmp_path: Path) -> None:
    _seed(tmp_path)
    _write_status(tmp_path, WARDEN, "applied", fp="0" * 64)
    _age(tmp_path, WARDEN, 60)
    os.utime(tmp_path / ".catraz/.env", (0, 0))
    stale = reload_cmd.stale_services(tmp_path, {WARDEN: LONG_AGO})
    assert stale == {WARDEN: [reload_cmd.OUT_OF_SYNC_MARKER]}


def test_env_change_is_stale_even_when_applied(tmp_path: Path) -> None:
    _seed(tmp_path)
    _write_status(tmp_path, PROXY, "applied")
    stale = reload_cmd.stale_services(tmp_path, {PROXY: LONG_AGO})
    assert stale == {PROXY: [tmp_path / ".catraz/.env"]}


def test_unknown_falls_back_to_mtime_including_secrets(tmp_path: Path) -> None:
    _seed(tmp_path)
    start = datetime.datetime.now(UTC) - datetime.timedelta(seconds=30)
    _age(tmp_path, WARDEN, 60)
    os.utime(tmp_path / ".catraz/.env", (0, 0))
    (tmp_path / ".catraz/secrets/write_tokens").write_text("example.com w\n")
    stale = reload_cmd.stale_services(tmp_path, {WARDEN: start})
    assert stale == {WARDEN: [tmp_path / ".catraz/secrets/write_tokens"]}


def test_endpoint_host_change_is_stale(tmp_path: Path) -> None:
    _seed(tmp_path)
    _endpoints(tmp_path, "a.example")
    (tmp_path / ".catraz/compose.hosts.yml").write_text(compose.render_hosts_fragment([]))
    _write_status(tmp_path, WARDEN, "applied")
    os.utime(tmp_path / ".catraz/.env", (0, 0))
    stale = reload_cmd.stale_services(tmp_path, {WARDEN: LONG_AGO})
    assert stale == {WARDEN: [reload_cmd.ENDPOINT_HOSTS_MARKER]}


def _reload(
    monkeypatch: pytest.MonkeyPatch, root: Path, rows: list[dict[str, str]]
) -> tuple[int, list[list[str]]]:
    calls: list[list[str]] = []
    monkeypatch.setattr(compose, "prepare", lambda *a, **kw: ["docker", "compose"])
    monkeypatch.setattr(reload_cmd, "compose_ps", lambda *a, **kw: rows)
    monkeypatch.setattr(compose, "container_started_at", lambda root, name, **kw: LONG_AGO)

    def _run(root: Path, args: typing.Any, **kw: typing.Any) -> types.SimpleNamespace:
        calls.append(args)
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(compose, "run", _run)
    ns = typing.cast(argparse.Namespace, types.SimpleNamespace(print_only=False))
    return reload_cmd.cmd_reload(root, ns, _out()), calls


def test_cmd_reload_reports_rejected_without_recreate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(tmp_path)
    _write_status(tmp_path, WARDEN, "rejected", detail="bad quota")
    os.utime(tmp_path / ".catraz/.env", (0, 0))
    rc, calls = _reload(monkeypatch, tmp_path, [{"Service": WARDEN, "Name": "w"}])
    assert rc == EXIT_OK and calls == []
    assert "bad quota" in capsys.readouterr().err


def test_cmd_reload_endpoint_change_warns_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(tmp_path)
    _endpoints(tmp_path, "a.example")
    (tmp_path / ".catraz/compose.hosts.yml").write_text(compose.render_hosts_fragment([]))
    _write_status(tmp_path, WARDEN, "applied")
    os.utime(tmp_path / ".catraz/.env", (0, 0))
    rows = [{"Service": WARDEN, "Name": "w"}, {"Service": "claude-dev-env", "Name": "a"}]
    rc, calls = _reload(monkeypatch, tmp_path, rows)
    assert rc == EXIT_OK
    assert calls == [["up", "-d", "--force-recreate", "--build", WARDEN]]
    assert "restart the agent" in capsys.readouterr().err


# ── status / run / allow wiring ───────────────────────────────────────────────


def _status(monkeypatch: pytest.MonkeyPatch, root: Path) -> int:
    rows = [
        {"Service": WARDEN, "State": "running", "Health": "healthy"},
        {"Service": PROXY, "State": "running", "Health": "healthy"},
    ]
    monkeypatch.setattr(stack, "compose_ps", lambda *a, **kw: rows)
    monkeypatch.setattr(stack, "_print_urls", lambda out: None)
    monkeypatch.setattr(compose, "prepare", lambda *a, **kw: ["docker", "compose"])
    ns = typing.cast(argparse.Namespace, types.SimpleNamespace())
    return stack.cmd_status(root, ns, _out())


def test_status_shows_applied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(tmp_path)
    _write_status(tmp_path, WARDEN, "applied")
    _write_status(tmp_path, PROXY, "applied")
    assert _status(monkeypatch, tmp_path) == EXIT_OK
    assert capsys.readouterr().out.count("applied live") == 2


def test_status_rejected_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(tmp_path)
    _write_status(tmp_path, WARDEN, "applied")
    _write_status(tmp_path, PROXY, "rejected", detail="FATAL: x")
    assert _status(monkeypatch, tmp_path) == EXIT_GENERAL
    out = capsys.readouterr().out
    assert "REJECTED" in out and "FATAL: x" in out


def test_status_unknown_is_a_hint_not_a_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(tmp_path)
    assert _status(monkeypatch, tmp_path) == EXIT_OK
    assert "no live status" in capsys.readouterr().out


def test_ensure_infra_reports_rejection_and_builds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(tmp_path)
    _write_status(tmp_path, WARDEN, "rejected", detail="bad toml")
    calls: list[list[str]] = []
    monkeypatch.setattr(run_cmd, "compose_ps", lambda *a, **kw: [])
    monkeypatch.setattr(run_cmd, "_security_preflight", lambda root, out: False)
    monkeypatch.setattr(run_cmd, "_auto_sync_if_needed", lambda root, out: None)
    monkeypatch.setattr(run_cmd, "compose_run", lambda root, args, **kw: calls.append(args))
    run_cmd._ensure_infra(tmp_path, _out())
    assert "bad toml" in capsys.readouterr().err
    assert calls == [["up", "-d", "--build", "--quiet-build"]]


@pytest.mark.parametrize(
    ("running", "settled", "expected"),
    [
        (False, None, "applies when the warden next starts"),
        (True, live_config.APPLIED, "applied to the running warden"),
        (True, live_config.PENDING, "has not confirmed"),
        (True, live_config.REJECTED, "config REJECTED"),
    ],
)
def test_allow_confirms_live_apply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    running: bool,
    settled: str | None,
    expected: str,
) -> None:
    _seed(tmp_path)
    monkeypatch.setattr(live_config, "warden_running", lambda root: running)
    if settled is not None:
        monkeypatch.setattr(
            live_config, "wait_settled", lambda root, svc: live_config.LiveState(svc, settled)
        )
    if settled == live_config.REJECTED:
        _write_status(tmp_path, WARDEN, "rejected", detail="d")
    setup._confirm_live_apply(tmp_path, _out(), running)
    captured = capsys.readouterr()
    assert expected in captured.out + captured.err
