"""supervise.py: config edits replace the warden child; invalid config → no child."""

from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path

import pytest

import warden.__main__ as main_mod
from warden import supervise
from warden.supervise import Supervisor

VALID = '[git.rules]\nbranch_prefixes = ["claude/"]\n'
VALID_NARROWER = '[git.rules]\nbranch_prefixes = ["bot/"]\n'
INVALID = "[git.rules]\nbranch_prefixes = []\n"

# Shared with the host CLI's fingerprint test: files b"a", b"b", missing.
KNOWN_VECTOR = "833d05d3c2e7c8c503cea30a28bbf06de844a3176923399f5424cf222fbbfbd8"


class FakeChild:
    def __init__(self) -> None:
        self.rc: int | None = None
        self.signals: list[int] = []
        self.killed = False
        self.hang = False

    def poll(self) -> int | None:
        return self.rc

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)
        if not self.hang:
            self.rc = -sig

    def wait(self, timeout: float | None = None) -> int:
        if self.rc is None:
            raise subprocess.TimeoutExpired("warden", timeout or 0)
        return self.rc

    def kill(self) -> None:
        self.killed = True
        self.rc = -9


@pytest.fixture
def setup(tmp_path: Path):
    toml = tmp_path / "warden.toml"
    toml.write_text(VALID)
    read = tmp_path / "read_tokens"
    read.write_text("gitlab.com tok\n")
    (tmp_path / "write_tokens").write_text("")
    env = {
        "WARDEN_CONFIG_PATH": str(toml),
        "READ_TOKENS_FILE": str(read),
        "WRITE_TOKENS_FILE": str(tmp_path / "write_tokens"),
    }
    children: list[FakeChild] = []

    def spawn(_env):
        child = FakeChild()
        children.append(child)
        return child

    status = tmp_path / "config-status"
    sup = Supervisor(env, spawn=spawn, status_path=status)
    return sup, toml, read, status, children


def _status(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text().splitlines())


def test_fingerprint_known_vector(tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"a")
    (tmp_path / "b").write_bytes(b"b")
    paths = [tmp_path / "a", tmp_path / "b", tmp_path / "missing"]
    assert supervise.fingerprint(paths) == KNOWN_VECTOR


def test_fingerprint_unset_input_is_dash(tmp_path: Path) -> None:
    assert supervise.fingerprint([None]) == supervise.fingerprint([tmp_path / "nope"])


def test_config_inputs_order_and_defaults() -> None:
    paths = supervise.config_inputs({"READ_TOKENS_FILE": "/r"})
    assert paths == [Path(supervise.DEFAULT_TOML_PATH), Path("/r"), None]


def test_start_valid_spawns_and_reports_applied(setup) -> None:
    sup, _, _, status, children = setup
    sup.start()
    assert len(children) == 1
    st = _status(status)
    assert st["state"] == "applied"
    assert st["fingerprint"] == sup._fingerprint()
    assert st["detail"] == ""


def test_start_invalid_spawns_nothing(setup) -> None:
    sup, toml, _, status, children = setup
    toml.write_text(INVALID)
    sup.start()
    assert children == []
    st = _status(status)
    assert st["state"] == "rejected"
    assert "branch_prefixes" in st["detail"]


def test_change_applies_only_after_stable_poll(setup) -> None:
    sup, toml, _, status, children = setup
    sup.start()
    toml.write_text(VALID_NARROWER)
    assert sup.tick() is None
    assert len(children) == 1  # first sighting: debounce
    assert sup.tick() is None
    assert len(children) == 2
    assert children[0].signals == [signal.SIGTERM]
    assert _status(status)["fingerprint"] == sup._fingerprint()


def test_flapping_file_is_not_applied(setup) -> None:
    sup, toml, _, _, children = setup
    sup.start()
    toml.write_text(VALID_NARROWER)
    sup.tick()
    toml.write_text(VALID_NARROWER + "\n")
    sup.tick()
    assert len(children) == 1
    sup.tick()
    assert len(children) == 2


def test_unchanged_does_nothing(setup) -> None:
    sup, _, _, _, children = setup
    sup.start()
    for _ in range(3):
        assert sup.tick() is None
    assert len(children) == 1 and children[0].signals == []


def test_revert_during_debounce_does_not_restart(setup) -> None:
    sup, toml, _, _, children = setup
    sup.start()
    toml.write_text(VALID_NARROWER)
    sup.tick()
    toml.write_text(VALID)
    sup.tick()
    sup.tick()
    assert len(children) == 1


def test_invalid_edit_stops_running_warden(setup) -> None:
    """Deny-all: the old, still-valid policy must not keep serving."""
    sup, toml, _, status, children = setup
    sup.start()
    toml.write_text(INVALID)
    sup.tick()
    sup.tick()
    assert children[0].signals == [signal.SIGTERM]
    assert sup.child is None and len(children) == 1
    assert _status(status)["state"] == "rejected"


def test_fixing_invalid_config_recovers(setup) -> None:
    sup, toml, _, status, children = setup
    toml.write_text(INVALID)
    sup.start()
    toml.write_text(VALID)
    sup.tick()
    sup.tick()
    assert len(children) == 1
    assert _status(status)["state"] == "applied"


def test_secret_change_restarts(setup) -> None:
    sup, _, read, _, children = setup
    sup.start()
    read.write_text("gitlab.com other\n")
    sup.tick()
    sup.tick()
    assert len(children) == 2


def test_malformed_secret_rejected(setup) -> None:
    sup, _, read, status, children = setup
    sup.start()
    read.write_text("gitlab.com a\ngitlab.com b\n")
    sup.tick()
    sup.tick()
    assert sup.child is None
    assert "duplicate host" in _status(status)["detail"]


def test_child_config_abort_enters_deny_all(setup) -> None:
    sup, _, _, status, children = setup
    sup.start()
    children[0].rc = supervise.CONFIG_ABORT_EXIT
    assert sup.tick() is None
    assert sup.child is None
    assert _status(status)["state"] == "rejected"


@pytest.mark.parametrize(("rc", "expected"), [(1, 1), (3, 3), (-11, 1)])
def test_child_crash_ends_supervisor(setup, rc: int, expected: int) -> None:
    sup, _, _, _, children = setup
    sup.start()
    children[0].rc = rc
    assert sup.tick() == expected


def test_hung_child_is_killed(setup) -> None:
    sup, toml, _, _, children = setup
    sup.start()
    children[0].hang = True
    sup.stop_child()
    assert children[0].killed


def test_no_status_path_is_fine(tmp_path: Path) -> None:
    supervise.write_status(None, "applied", "x")  # no-op


def test_status_write_failure_is_logged(tmp_path: Path, caplog) -> None:
    supervise.write_status(tmp_path / "missing-dir" / "s", "applied", "x")
    assert "cannot write config status" in caplog.text


def test_status_detail_is_one_line(tmp_path: Path) -> None:
    path = tmp_path / "s"
    supervise.write_status(path, "rejected", "fp", "a\nb\tc")
    assert _status(path)["detail"] == "a b c"


def test_run_loops_until_signal(setup) -> None:
    sup, toml, _, _, children = setup
    calls = {"n": 0}

    def sleep(_s: float) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            toml.write_text(VALID_NARROWER)
        if calls["n"] == 5:
            signal.raise_signal(signal.SIGTERM)

    old = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    try:
        assert sup.run(0.0, sleep=sleep) == 0
    finally:
        signal.signal(signal.SIGTERM, old[0])
        signal.signal(signal.SIGINT, old[1])
    assert len(children) == 2
    assert children[1].signals == [signal.SIGTERM]  # stopped on the way out


def test_run_returns_child_crash_code(setup) -> None:
    sup, _, _, _, children = setup

    def sleep(_s: float) -> None:
        children[0].rc = 7

    old = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    try:
        assert sup.run(0.0, sleep=sleep) == 7
    finally:
        signal.signal(signal.SIGTERM, old[0])
        signal.signal(signal.SIGINT, old[1])


def test_real_process_stop(tmp_path: Path) -> None:
    def spawn(_env):
        return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])

    sup = Supervisor({"WARDEN_CONFIG_PATH": str(tmp_path / "none.toml")}, spawn=spawn)
    sup.start()
    child = sup.child
    assert child is not None and child.poll() is None
    sup.stop_child()
    assert child.poll() is not None


def test_main_wires_env(monkeypatch, tmp_path: Path) -> None:
    seen = {}

    def fake_run(self, poll_s, sleep=None):
        seen["poll"] = poll_s
        seen["status"] = self.status_path
        return 0

    monkeypatch.setattr(Supervisor, "run", fake_run)
    env = {"CONFIG_STATUS_FILE": str(tmp_path / "s"), "CONFIG_POLL_S": "0.5"}
    assert supervise.main(env) == 0
    assert seen == {"poll": 0.5, "status": tmp_path / "s"}
    for bad in ("x", "-1"):
        supervise.main({"CONFIG_POLL_S": bad})
        assert seen["poll"] == 1.0 and seen["status"] is None


def test_dunder_main_dispatches_supervise(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["warden", "supervise"])
    monkeypatch.setattr(supervise, "main", lambda: 0)
    with pytest.raises(SystemExit) as exc:
        main_mod.main()
    assert exc.value.code == 0
