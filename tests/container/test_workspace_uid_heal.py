"""Workspace-ownership repair. Windows bind mounts carry no host ownership, so
files a previous DEV_UID wrote stay unwritable for the current one; on a POSIX
host the files belong to the host user and must be left alone."""

import os
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(not hasattr(os, "chown"), reason="container code — POSIX only")


@pytest.fixture
def workspace(ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the entrypoint's fixed container paths at a temp tree."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (tmp_path / "logs").mkdir()
    monkeypatch.setattr(ep, "WORKSPACE", ws)
    monkeypatch.setattr(ep, "AGENT_LOG_DIR", tmp_path / "logs")
    return ws


def _record_chown_tree(ep: Any, monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(ep, "_chown_tree", lambda top, uid, gid: calls.append((uid, gid)))
    return calls


def test_posix_host_never_touches_workspace_ownership(
    ep: Any, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CATRAZ_HOST_OS", "posix")
    calls = _record_chown_tree(ep, monkeypatch)
    ep.heal_workspace_ownership(1000, 1000)
    assert calls == []


def test_unset_host_os_never_touches_workspace_ownership(
    ep: Any, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare `docker run` of the image gets no contract — repair stays off."""
    monkeypatch.delenv("CATRAZ_HOST_OS", raising=False)
    calls = _record_chown_tree(ep, monkeypatch)
    ep.heal_workspace_ownership(1000, 1000)
    assert calls == []


def test_windows_host_reowns_the_workspace_once_per_uid(
    ep: Any, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CATRAZ_HOST_OS", "windows")
    calls = _record_chown_tree(ep, monkeypatch)

    ep.heal_workspace_ownership(1000, 1000)
    assert calls == [(1000, 1000)]

    ep.heal_workspace_ownership(1000, 1000)  # stamp matches → no second walk
    assert calls == [(1000, 1000)]

    ep.heal_workspace_ownership(1001, 1001)  # uid changed → files are stale again
    assert calls == [(1000, 1000), (1001, 1001)]


def test_chown_tree_spares_mount_points_and_symlink_targets(
    ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The .catraz tmpfs shadow is a mount point and keeps its root-owned 0700;
    a symlink planted in the workspace must not hand its target to the dev user."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("x")
    (tmp_path / "shadow").mkdir()
    (tmp_path / "shadow" / "secret").write_text("x")
    (tmp_path / "escape").symlink_to("/etc/passwd")

    monkeypatch.setattr(os.path, "ismount", lambda p: Path(p).name == "shadow")
    chowned: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        os,
        "chown",
        lambda p, u, g, follow_symlinks=True: chowned.append((Path(p).name, follow_symlinks)),
    )

    ep._chown_tree(tmp_path, 1000, 1000)

    names = {name for name, _ in chowned}
    assert {"src", "main.py"} <= names
    assert "shadow" not in names and "secret" not in names
    assert ("escape", False) in chowned
