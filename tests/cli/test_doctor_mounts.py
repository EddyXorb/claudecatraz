import subprocess
from pathlib import Path
from typing import Any

import pytest

from catraz import doctor


def _mkdirs(root: Path) -> None:
    for rel in ("state/warden/db", "logs/warden", "state/warden/run", "logs/squid"):
        (root / ".catraz" / rel).mkdir(parents=True)
    for rel in ("config/warden.toml", "config/squid.conf", "config/allowlist.txt"):
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

    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: R(0, "999999999"))
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
