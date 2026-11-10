"""forward-proxy/squid-supervise.sh against a fake squid binary."""

from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable, Iterator

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "forward-proxy" / "squid-supervise.sh"

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
    proc = subprocess.Popen(["/bin/sh", str(SCRIPT)], env=env, stderr=subprocess.PIPE)
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
    assert entry.rstrip().endswith("exec /usr/local/bin/squid-supervise.sh")
    compose = (repo / "src/catraz/assets/compose/docker-compose.yml").read_text()
    assert "${PROJECT_DIR}/.catraz/config:/etc/catraz/config:ro" in compose
    assert "${PROJECT_DIR}/.catraz/config:/etc/warden/config:ro" in compose
    assert "WARDEN_CONFIG_PATH=/etc/warden/config/warden.toml" in compose
    assert ":/etc/squid/" not in compose  # no inode-pinning file mounts
    warden_entry = (repo / "warden" / "docker-entrypoint.sh").read_text()
    assert warden_entry.rstrip().endswith("exec gosu warden python -m warden supervise")
