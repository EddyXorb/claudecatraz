import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from catraz import doctor


def _mounts_json(destinations: Any) -> str:
    return json.dumps([{"Destination": d} for d in destinations])


def _mkdirs(root: Path) -> None:
    for rel in ("state/warden/db", "logs/warden", "state/warden/run", "logs/squid"):
        (root / ".catraz" / rel).mkdir(parents=True)
    for rel in (
        "config/warden.toml",
        "config/squid.conf",
        "config/allowlist.txt",
        "secrets/read_tokens",
        "secrets/write_tokens",
    ):
        p = root / ".catraz" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()


def test_check_mounts_stack_down_is_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "which", lambda c: True)
    monkeypatch.setattr(doctor, "compose_ps", lambda root: [])
    f = doctor.Findings()
    doctor.check_mounts(tmp_path, f)
    assert not any(i[0] == doctor.BAD for i in f.items)
    assert any("nothing to verify" in i[2] for i in f.items)


def test_check_mounts_matching_inode_is_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _mkdirs(tmp_path)
    monkeypatch.setattr(doctor, "which", lambda c: True)
    monkeypatch.setattr(
        doctor,
        "compose_ps",
        lambda root: [
            {"Service": "gitlab-warden", "Name": "warden-1"},
            {"Service": "forward-proxy", "Name": "proxy-1"},
        ],
    )

    container_to_rel = {
        container_path: rel
        for targets in doctor.MOUNT_TARGETS.values()
        for rel, container_path in targets
    }

    class R:
        def __init__(s: Any, rc: int, out: str = "") -> None:
            s.returncode, s.stdout = rc, out

    def fake_run(cmd: list[str], **k: Any) -> R:
        if cmd[1] == "inspect":
            return R(0, _mounts_json(container_to_rel))
        rel = container_to_rel[cmd[-1]]
        return R(0, str((tmp_path / ".catraz" / rel).stat().st_ino))

    monkeypatch.setattr(subprocess, "run", fake_run)
    f = doctor.Findings()
    doctor.check_mounts(tmp_path, f)
    assert not any(i[0] == doctor.BAD for i in f.items)
    assert not any(i[0] == doctor.WARN for i in f.items)
    expected = sum(len(v) for v in doctor.MOUNT_TARGETS.values())
    assert sum(1 for i in f.items if i[0] == doctor.OK) == expected


def test_check_mounts_mismatched_inode_is_bad(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mkdirs(tmp_path)
    monkeypatch.setattr(doctor, "which", lambda c: True)
    monkeypatch.setattr(
        doctor,
        "compose_ps",
        lambda root: [{"Service": "forward-proxy", "Name": "proxy-1"}],
    )

    class R:
        def __init__(s: Any, rc: int, out: str = "") -> None:
            s.returncode, s.stdout = rc, out

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **k: R(1) if cmd[1] == "inspect" else R(0, "999999999"),
    )
    f = doctor.Findings()
    doctor.check_mounts(tmp_path, f)
    bad = [i for i in f.items if i[0] == doctor.BAD]
    assert len(bad) == len(doctor.MOUNT_TARGETS["forward-proxy"])
    assert bad[0][3] is not None and "catraz reload --force" in bad[0][3]


def test_check_mounts_docker_missing_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "which", lambda c: False)
    f = doctor.Findings()
    doctor.check_mounts(tmp_path, f)
    assert any(i[0] == doctor.WARN for i in f.items)


def test_check_mounts_missing_mount_is_bad_with_reload_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A container created from an older compose wiring lacks the mount point."""
    _mkdirs(tmp_path)
    monkeypatch.setattr(doctor, "which", lambda c: True)
    monkeypatch.setattr(
        doctor,
        "compose_ps",
        lambda root: [{"Service": "forward-proxy", "Name": "proxy-1"}],
    )

    class R:
        def __init__(s: Any, rc: int, out: str = "") -> None:
            s.returncode, s.stdout = rc, out

    def fake_run(cmd: list[str], **k: Any) -> R:
        if cmd[1] == "inspect":
            return R(0, _mounts_json(["/var/log/squid", "/etc/squid/squid.conf"]))
        if cmd[-1] == "/var/log/squid":
            return R(0, str((tmp_path / ".catraz/logs/squid").stat().st_ino))
        return R(1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    f = doctor.Findings()
    doctor.check_mounts(tmp_path, f)
    bad = [i for i in f.items if i[0] == doctor.BAD]
    assert len(bad) == 1
    assert "/etc/catraz/config" in bad[0][2]
    assert bad[0][3] == "catraz reload --force"
    assert not any(i[0] == doctor.WARN for i in f.items)


def test_check_mounts_unstattable_existing_mount_warns_without_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mounted but not stat-able has no single likely cause, so no fix is suggested."""
    _mkdirs(tmp_path)
    monkeypatch.setattr(doctor, "which", lambda c: True)
    monkeypatch.setattr(
        doctor,
        "compose_ps",
        lambda root: [{"Service": "forward-proxy", "Name": "proxy-1"}],
    )

    class R:
        def __init__(s: Any, rc: int, out: str = "") -> None:
            s.returncode, s.stdout = rc, out

    targets = [c for _, c in doctor.MOUNT_TARGETS["forward-proxy"]]
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **k: R(0, _mounts_json(targets)) if cmd[1] == "inspect" else R(1),
    )
    f = doctor.Findings()
    doctor.check_mounts(tmp_path, f)
    warns = [i for i in f.items if i[0] == doctor.WARN]
    assert len(warns) == len(targets)
    assert all(i[3] is None for i in warns)
