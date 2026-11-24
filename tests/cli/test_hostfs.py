"""A host without POSIX uids (Windows): bind-mount ownership and file modes are
synthetic there, so DEV_UID keeps its seeded value and every uid/mode/chown path
is skipped rather than guessed."""

import argparse
import os
from pathlib import Path

import pytest

from catraz import admin_client, compose, doctor, hostfs, live_config
from catraz.commands import observe
from catraz.commands.setup import _init_seed_env
from catraz.errors import EXIT_GENERAL
from catraz.ui import Out


@pytest.fixture
def no_host_uid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(os, "getuid", raising=False)


def _make_root(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".catraz").mkdir(parents=True)
    (root / ".catraz" / ".env").write_text("DEV_UID=1000\nAUTH_MODE=subscription\n")
    return root


def test_host_os_is_windows_without_a_host_uid(no_host_uid: None) -> None:
    assert hostfs.host_uid() is None
    assert hostfs.host_os() == "windows"


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX host only")
def test_host_uid_is_the_process_uid() -> None:
    assert hostfs.host_uid() == os.getuid()
    assert hostfs.host_os() == "posix"


def test_seed_env_keeps_dev_uid_without_a_host_uid(tmp_path: Path, no_host_uid: None) -> None:
    """No uid to match → the seeded DEV_UID stands; deriving one would only give
    it a way to drift, and a changed uid locks the agent out of its own files."""
    root = _make_root(tmp_path)
    cat = root / ".catraz"
    _, updates = _init_seed_env(cat, tmp_path, cat / ".env", Out(color=False))
    assert "DEV_UID" not in updates


def test_check_env_does_not_flag_synthetic_ownership(tmp_path: Path, no_host_uid: None) -> None:
    root = _make_root(tmp_path)
    (root / ".catraz" / "state").mkdir()
    (root / ".catraz" / "logs").mkdir()
    f = doctor.Findings()
    doctor.check_env(root, {"DEV_UID": "1000"}, f)
    assert not [i for i in f.items if i[0] == doctor.BAD]
    assert [i for i in f.items if i[0] == doctor.WARN and "synthetic" in i[2]]


def test_doctor_fix_skips_chown_without_a_host_uid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_host_uid: None
) -> None:
    """os.chown does not exist on such a host — removing it here turns a missing
    guard into a failure instead of a silently platform-dependent pass."""
    monkeypatch.delattr(os, "chown", raising=False)
    root = _make_root(tmp_path)
    doctor._doctor_fix(root, {"DEV_UID": "1000", "AUTH_MODE": "subscription"})
    assert (root / ".catraz" / "logs" / "warden").is_dir()


def test_compose_env_hands_the_ownership_contract_to_the_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_host_uid: None
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert compose._compose_env(_make_root(tmp_path), None)["CATRAZ_HOST_OS"] == "windows"


# ── no Unix sockets (CPython on Windows) ─────────────────────────────────────


@pytest.fixture
def no_unix_sockets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A present socket file, so only the missing AF_UNIX can stop the callers."""
    monkeypatch.setattr(hostfs, "HAS_UNIX_SOCKETS", False)
    root = _make_root(tmp_path)
    sock = admin_client.admin_socket_path(root)
    sock.parent.mkdir(parents=True)
    sock.touch()
    return root


def test_warden_counts_as_down_without_unix_sockets(no_unix_sockets: Path) -> None:
    assert live_config.warden_running(no_unix_sockets) is False


def test_admin_client_reports_unreachable_without_unix_sockets(no_unix_sockets: Path) -> None:
    with pytest.raises(admin_client.AdminUnreachable, match="unavailable on this host"):
        admin_client.get_json(no_unix_sockets, "/policy")


def test_audit_web_refuses_without_unix_sockets(
    no_unix_sockets: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = argparse.Namespace(web=True)
    assert observe.cmd_audit(no_unix_sockets, args, Out(color=False)) == EXIT_GENERAL
    assert "Unix sockets" in capsys.readouterr().err
