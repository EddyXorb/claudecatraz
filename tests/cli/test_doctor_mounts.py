import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from catraz import compose, doctor

RELS = ("logs/squid", "config", "secrets/read_tokens")


class R:
    def __init__(self, rc: int, out: str = "") -> None:
        self.returncode, self.stdout = rc, out


@pytest.fixture(autouse=True)
def _stack(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "which", lambda c: True)
    monkeypatch.setattr(
        doctor, "compose_ps", lambda root: [{"Service": "forward-proxy", "Name": "p"}]
    )
    monkeypatch.setattr(compose, "prepare", lambda root, **kw: ["docker", "compose"])
    monkeypatch.setattr(compose, "drifted_services", lambda root, rows, **kw: set())


def _mounts(root: Path) -> list[dict[str, str]]:
    mounts = [{"Type": "volume", "Source": "/var/lib/docker/v", "Destination": "/v"}]
    for rel in RELS:
        p = root / ".catraz" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch() if rel.startswith("secrets") else p.mkdir()
        mounts.append({"Type": "bind", "Source": str(p), "Destination": f"/c/{rel}"})
    return mounts


def _docker(monkeypatch: pytest.MonkeyPatch, mounts: Any, inode: Any) -> None:
    """`inode(destination)` answers `docker exec … stat`; None makes it fail."""

    def fake_run(cmd: list[str], **k: Any) -> R:
        if cmd[1] == "inspect":
            return R(0, json.dumps(mounts)) if mounts is not None else R(1)
        ino = inode(cmd[-1])
        return R(0, str(ino)) if ino is not None else R(1)

    monkeypatch.setattr(subprocess, "run", fake_run)


def _by_dest(root: Path) -> dict[str, int]:
    return {f"/c/{rel}": (root / ".catraz" / rel).stat().st_ino for rel in RELS}


def test_stack_down_is_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "compose_ps", lambda root: [])
    f = doctor.Findings()
    doctor.check_mounts(tmp_path, f)
    assert [(i[0], i[2]) for i in f.items] == [(doctor.OK, "stack not running — nothing to verify")]


def test_every_inspected_bind_mount_is_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mounts = _mounts(tmp_path)
    _docker(monkeypatch, mounts, _by_dest(tmp_path).get)
    f = doctor.Findings()
    doctor.check_mounts(tmp_path, f)
    assert [i[0] for i in f.items] == [doctor.OK] * len(RELS)
    assert ".catraz/secrets/read_tokens" in f.items[-1][2]


def test_recreated_host_path_is_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mounts = _mounts(tmp_path)
    _docker(monkeypatch, mounts, lambda dest: 999999999)
    f = doctor.Findings()
    doctor.check_mounts(tmp_path, f)
    assert [(i[0], i[3]) for i in f.items] == [(doctor.BAD, "catraz reload --force")] * len(RELS)


def test_missing_host_source_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gone = [{"Type": "bind", "Source": str(tmp_path / "gone"), "Destination": "/g"}]
    _docker(monkeypatch, gone, lambda dest: pytest.fail("no stat for a missing source"))
    f = doctor.Findings()
    doctor.check_mounts(tmp_path, f)
    assert f.items == []


def test_drifted_container_is_bad_with_reload_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(compose, "drifted_services", lambda root, rows, **kw: {"forward-proxy"})
    _docker(monkeypatch, None, lambda dest: pytest.fail("no stat on drift"))
    f = doctor.Findings()
    doctor.check_mounts(tmp_path, f)
    assert [(i[0], i[3]) for i in f.items] == [(doctor.BAD, "catraz reload")]


def test_unreadable_container_warns_without_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither side has a single likely cause, so no fix is suggested."""
    monkeypatch.setattr(compose, "drifted_services", lambda root, rows, **kw: None)
    mounts = _mounts(tmp_path)
    _docker(monkeypatch, mounts, lambda dest: None)
    f = doctor.Findings()
    doctor.check_mounts(tmp_path, f)
    assert [(i[0], i[3]) for i in f.items] == [(doctor.WARN, None)] * (1 + len(RELS))


def test_uninspectable_container_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _docker(monkeypatch, None, lambda dest: None)
    f = doctor.Findings()
    doctor.check_mounts(tmp_path, f)
    assert [(i[0], i[3]) for i in f.items] == [(doctor.WARN, None)]


def test_docker_missing_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "which", lambda c: False)
    f = doctor.Findings()
    doctor.check_mounts(tmp_path, f)
    assert [i[0] for i in f.items] == [doctor.WARN]
