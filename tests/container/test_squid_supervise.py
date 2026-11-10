"""forward-proxy/squid_supervise.py: state machine with fakes, process against a fake squid."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Callable, Iterator, Optional

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "forward-proxy" / "squid_supervise.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("squid_supervise", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["squid_supervise"] = mod
    spec.loader.exec_module(mod)
    return mod


sup_mod = _load()

FAKE_SQUID = """#!/bin/sh
dir=$(dirname "$0")
if [ "$1" = "-k" ] && [ "$2" = "parse" ]; then
  if grep -q INVALID "$4" "$dir/allowlist.txt" 2>/dev/null; then
    echo "2026/01/01 WARNING: noise"
    echo "2026/01/01 FATAL: Bungled squid.conf line 3"
    echo "trailing"
    exit 1
  fi
  exit 0
fi
echo start >> "$dir/events"
trap 'echo hup >> "$dir/events"' HUP
trap 'echo term >> "$dir/events"; exit 0' TERM
while :; do
  [ -e "$dir/crash" ] && exit 3
  sleep 0.05
done
"""


def _wait(cond: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached")


class Harness:
    def __init__(self, d: Path, proc: subprocess.Popen[bytes]) -> None:
        self.d = d
        self.proc = proc

    def events(self) -> list[str]:
        p = self.d / "events"
        return p.read_text().split() if p.exists() else []

    def status(self) -> dict[str, str]:
        p = self.d / "status"
        if not p.exists():
            return {}
        return dict(line.split("=", 1) for line in p.read_text().splitlines())

    def fingerprint(self) -> str:
        outer = hashlib.sha256()
        for name in ("squid.conf", "allowlist.txt"):
            data = (self.d / name).read_bytes()
            outer.update(f"{hashlib.sha256(data).hexdigest()}\n".encode())
        return outer.hexdigest()


def _start(d: Path, conf: str = "http_port 3128\n") -> Harness:
    (d / "squid.conf").write_text(conf)
    (d / "allowlist.txt").write_text(".example.com\n")
    fake = d / "squid"
    fake.write_text(FAKE_SQUID)
    fake.chmod(0o755)
    env = dict(
        os.environ,
        SQUID_BIN=str(fake),
        SQUID_CONF=str(d / "squid.conf"),
        SQUID_WATCH=f"{d / 'squid.conf'} {d / 'allowlist.txt'}",
        CONFIG_POLL_S="0.1",
        CONFIG_STATUS_FILE=str(d / "status"),
    )
    proc = subprocess.Popen([sys.executable, str(SCRIPT)], env=env, stderr=subprocess.PIPE)
    return Harness(d, proc)


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    h = _start(tmp_path)
    yield h
    if h.proc.poll() is None:
        h.proc.kill()
        h.proc.wait()
    (tmp_path / "crash").touch()  # let any orphaned fake squid exit


def test_starts_squid_and_reports_applied(harness: Harness) -> None:
    _wait(lambda: harness.events() == ["start"] and harness.status().get("state") == "applied")
    assert harness.status()["fingerprint"] == harness.fingerprint()


def test_allowlist_edit_reconfigures_in_place(harness: Harness) -> None:
    _wait(lambda: harness.status().get("state") == "applied")
    (harness.d / "allowlist.txt").write_text(".example.com\n.pypi.org\n")
    _wait(lambda: harness.events() == ["start", "hup"])
    _wait(lambda: harness.status().get("fingerprint") == harness.fingerprint())


def test_invalid_edit_stops_squid_then_recovers(harness: Harness) -> None:
    _wait(lambda: harness.status().get("state") == "applied")
    (harness.d / "allowlist.txt").write_text("INVALID\n")
    _wait(lambda: harness.status().get("state") == "rejected")
    assert harness.events() == ["start", "term"]
    assert harness.status()["detail"] == "2026/01/01 FATAL: Bungled squid.conf line 3"
    assert harness.status()["fingerprint"] == harness.fingerprint()
    (harness.d / "allowlist.txt").write_text(".example.com\n")
    _wait(lambda: harness.status().get("state") == "applied")
    _wait(lambda: harness.events() == ["start", "term", "start"])


def test_invalid_at_start_runs_no_squid(tmp_path: Path) -> None:
    h = _start(tmp_path, conf="INVALID\n")
    try:
        _wait(lambda: h.status().get("state") == "rejected")
        time.sleep(0.3)
        assert h.events() == []
        assert h.proc.poll() is None  # stays up, watching for a fix
    finally:
        h.proc.kill()
        h.proc.wait()


def test_squid_crash_ends_supervisor(harness: Harness) -> None:
    _wait(lambda: harness.events() == ["start"])
    (harness.d / "crash").touch()
    assert harness.proc.wait(timeout=5) == 3


def test_sigterm_stops_squid(harness: Harness) -> None:
    _wait(lambda: harness.events() == ["start"])
    harness.proc.send_signal(signal.SIGTERM)
    assert harness.proc.wait(timeout=5) == 0
    assert harness.events() == ["start", "term"]


def test_image_and_compose_wiring() -> None:
    """squid reads through links into a directory mount; the entrypoints hand off."""
    repo = SCRIPT.parents[1]
    dockerfile = (repo / "forward-proxy" / "Dockerfile").read_text()
    assert "ln -sf /etc/catraz/config/squid.conf /etc/squid/squid.conf" in dockerfile
    assert "ln -sf /etc/catraz/config/allowlist.txt /etc/squid/allowlist.txt" in dockerfile
    entry = (repo / "forward-proxy" / "docker-entrypoint.sh").read_text()
    assert entry.rstrip().endswith("exec python3 /usr/local/bin/squid_supervise.py")
    assert "squid_supervise.py" in dockerfile and "python3" in dockerfile
    compose = (repo / "src/catraz/assets/compose/docker-compose.yml").read_text()
    assert "${PROJECT_DIR}/.catraz/config:/etc/catraz/config:ro" in compose
    assert "${PROJECT_DIR}/.catraz/config:/etc/warden/config:ro" in compose
    assert "WARDEN_CONFIG_PATH=/etc/warden/config/warden.toml" in compose
    assert ":/etc/squid/" not in compose  # no inode-pinning file mounts
    warden_entry = (repo / "warden" / "docker-entrypoint.sh").read_text()
    assert warden_entry.rstrip().endswith("exec gosu warden python -m warden supervise")


# --- state machine (no processes, no timing) -----------------------------------


class FakeChild:
    def __init__(self, stubborn: bool = False) -> None:
        self.signals: list[int] = []
        self.rc: Optional[int] = None
        self.stubborn = stubborn
        self.killed = False

    def poll(self) -> Optional[int]:
        return self.rc

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)
        if sig == signal.SIGTERM and not self.stubborn:
            self.rc = 0

    def wait(self, timeout: Optional[float] = None) -> int:
        if self.rc is None and timeout is not None:
            raise subprocess.TimeoutExpired("squid", timeout)
        return self.rc if self.rc is not None else -9

    def kill(self) -> None:
        self.killed = True
        self.rc = -9


class Rig:
    def __init__(self, d: Path) -> None:
        self.conf = d / "squid.conf"
        self.allow = d / "allowlist.txt"
        self.status = d / "status"
        self.conf.write_text("http_port 3128\n")
        self.allow.write_text(".example.com\n")
        self.reason: Optional[str] = None
        self.children: list[FakeChild] = []
        self.sup = sup_mod.Supervisor(
            [self.conf, self.allow],
            check=lambda: self.reason,
            spawn=self._spawn,
            status_path=self.status,
            stop_timeout_s=0.01,
        )

    def _spawn(self) -> FakeChild:
        self.children.append(FakeChild())
        return self.children[-1]

    def state(self) -> dict[str, str]:
        return dict(ln.split("=", 1) for ln in self.status.read_text().splitlines())

    def fp(self) -> str:
        return str(sup_mod.fingerprint([self.conf, self.allow]))


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def test_edit_applies_only_after_a_stable_poll(rig: Rig) -> None:
    rig.sup.start()
    first = rig.children[0]
    rig.allow.write_text(".example.com\n.pypi.org\n")
    assert rig.sup.tick() is None and first.signals == []  # seen once: pending
    assert rig.sup.tick() is None and first.signals == [signal.SIGHUP]
    assert rig.state() == {"state": "applied", "fingerprint": rig.fp(), "detail": ""}
    assert len(rig.children) == 1


def test_still_changing_file_waits(rig: Rig) -> None:
    rig.sup.start()
    rig.allow.write_text("a\n")
    rig.sup.tick()
    rig.allow.write_text("b\n")
    rig.sup.tick()
    assert rig.children[0].signals == []
    rig.sup.tick()
    assert rig.children[0].signals == [signal.SIGHUP]


def test_revert_before_apply_drops_the_pending_change(rig: Rig) -> None:
    rig.sup.start()
    rig.allow.write_text("a\n")
    rig.sup.tick()
    rig.allow.write_text(".example.com\n")
    rig.sup.tick()
    rig.sup.tick()
    assert rig.children[0].signals == []


def test_invalid_stops_squid_and_valid_restarts_it(rig: Rig) -> None:
    rig.sup.start()
    rig.reason = "FATAL: bad"
    rig.conf.write_text("broken\n")
    rig.sup.tick()
    rig.sup.tick()
    assert rig.children[0].signals == [signal.SIGTERM] and rig.sup.child is None
    assert rig.state() == {"state": "rejected", "fingerprint": rig.fp(), "detail": "FATAL: bad"}
    rig.reason = None
    rig.conf.write_text("http_port 3128\n")
    rig.sup.tick()
    rig.sup.tick()
    assert len(rig.children) == 2 and rig.state()["state"] == "applied"


def test_invalid_at_start_spawns_nothing(rig: Rig) -> None:
    rig.reason = "FATAL: bad"
    rig.sup.start()
    assert rig.children == [] and rig.state()["state"] == "rejected"
    assert rig.sup.tick() is None  # keeps watching


@pytest.mark.parametrize(("rc", "expected"), [(3, 3), (0, 1), (-15, 1)])
def test_squid_exit_ends_supervisor_nonzero(rig: Rig, rc: int, expected: int) -> None:
    rig.sup.start()
    rig.children[0].rc = rc
    assert rig.sup.tick() == expected


def test_stubborn_squid_is_killed(rig: Rig) -> None:
    rig.sup.start()
    rig.children[0].stubborn = True
    rig.sup.stop_child()
    assert rig.children[0].killed


def test_run_stops_child_on_sigterm(rig: Rig) -> None:
    def sleep(_s: float) -> None:
        os.kill(os.getpid(), signal.SIGTERM)

    old = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    try:
        assert rig.sup.run(0.01, sleep=sleep) == 0
    finally:
        signal.signal(signal.SIGTERM, old[0])
        signal.signal(signal.SIGINT, old[1])
    assert rig.children[0].signals == [signal.SIGTERM]


def test_run_returns_squid_exit_code(rig: Rig) -> None:
    def sleep(_s: float) -> None:
        rig.children[0].rc = 4

    old = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    try:
        assert rig.sup.run(0.01, sleep=sleep) == 4
    finally:
        signal.signal(signal.SIGTERM, old[0])
        signal.signal(signal.SIGINT, old[1])


def test_unreadable_file_fingerprints_as_dash(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    expected = hashlib.sha256(b"-\n").hexdigest()
    assert sup_mod.fingerprint([missing]) == expected


def test_fingerprint_matches_host_cli(tmp_path: Path) -> None:
    from catraz import live_config

    a, b = tmp_path / "squid.conf", tmp_path / "allowlist.txt"
    a.write_text("x\n")
    b.write_text("y\n")
    assert sup_mod.fingerprint([a, b]) == live_config.fingerprint([a, b])


@pytest.mark.parametrize(
    ("output", "reason"),
    [
        ("WARNING: x\nFATAL: bad line\ntrailing\n", "FATAL: bad line"),
        ("ERROR: one\nERROR: two\n", "ERROR: two"),
        ("just noise\nlast\n\n", "last"),
        ("", "squid -k parse failed"),
    ],
)
def test_parse_reason(output: str, reason: str) -> None:
    assert sup_mod.parse_reason(output) == reason


def test_squid_check_reports_missing_binary(tmp_path: Path) -> None:
    squid = sup_mod.Squid(str(tmp_path / "no-squid"), str(tmp_path / "squid.conf"))
    assert squid.check() is not None


def test_status_write_failure_is_not_fatal(tmp_path: Path) -> None:
    sup_mod.write_status(tmp_path / "missing-dir" / "status", "applied", "fp")
    sup_mod.write_status(None, "applied", "fp")


@pytest.mark.parametrize(
    ("raw", "expected"), [(None, 1.0), ("0.25", 0.25), ("0", 1.0), ("-1", 1.0), ("x", 1.0)]
)
def test_poll_interval(raw: Optional[str], expected: float) -> None:
    env = {} if raw is None else {"CONFIG_POLL_S": raw}
    assert sup_mod.poll_interval(env) == expected


def test_squid_check_against_fake_binary(tmp_path: Path) -> None:
    fake = tmp_path / "squid"
    fake.write_text(FAKE_SQUID)
    fake.chmod(0o755)
    conf = tmp_path / "squid.conf"
    conf.write_text("http_port 3128\n")
    squid = sup_mod.Squid(str(fake), str(conf))
    assert squid.check() is None
    conf.write_text("INVALID\n")
    assert squid.check() == "2026/01/01 FATAL: Bungled squid.conf line 3"
