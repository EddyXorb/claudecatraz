"""Red-Team tests for shadow mount protection.
T2, T7a, T8 run in CI (`-m "not slow"`, docker run alpine only); T1, T3, T4
are @slow and need a live catraz stack with Docker plus a real
ANTHROPIC_API_KEY. T7b (host-side symlink guard) lives in
tests/cli/test_invariants.py."""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterator
import pytest


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    r = subprocess.run(["docker", "info"], capture_output=True)
    return r.returncode == 0


pytestmark = pytest.mark.skipif(not _docker_available(), reason="needs docker")


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True)


# ── T2 — baseline: tmpfs ordering guarantee (gating test) ────────────────────


def test_t2_tmpfs_overdeck_ordering(tmp_path: Path) -> None:
    """tmpfs over a bind subpath masks host content deterministically."""
    (tmp_path / ".catraz").mkdir()
    (tmp_path / ".catraz/secret").write_text("TOP")
    r = _run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{tmp_path}:/workspace",
            "--tmpfs",
            "/workspace/.catraz",
            "alpine",
            "sh",
            "-c",
            "ls -A /workspace/.catraz | wc -l",
        ]
    )
    assert r.returncode == 0
    assert r.stdout.strip() == "0"  # .catraz appears EMPTY to the container


# ── Fixture: running stack via catraz init -y + up --remote ──────────────────


@pytest.fixture(scope="module")
def live_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Start a catraz stack in a temporary project dir; tear down after module.
    Requires Docker + a real ANTHROPIC_API_KEY; used only by @slow tests
    (T1/T3/T4). init runs with check=False (doctor may warn); .env is
    written after init so it isn't overwritten; DEV_UID matches the
    runner's uid."""
    root = tmp_path_factory.mktemp("catraz-proj")
    env = dict(os.environ, HOME=str(root))
    catraz = [sys.executable, "-m", "catraz"]

    subprocess.run(
        [*catraz, "-C", str(root), "init", "-y", "--skip-sync"], env=env, check=False
    )  # exit 3 from doctor tolerated; scaffold created

    # Write a complete .env after init (so it is not overwritten)
    (root / ".catraz" / ".env").write_text(
        "AUTH_MODE=api_key\n"
        "ANTHROPIC_API_KEY=" + os.environ.get("ANTHROPIC_API_KEY", "sk-ci-dummy") + "\n"
        "GITLAB_READ_TOKEN=ci-dummy\n"
        "GITLAB_WRITE_TOKEN=ci-dummy\n"
        "WARDEN_ALLOWED_PROJECTS=acme/demo\n"
        f"DEV_UID={os.getuid()}\n"
    )

    # `run claude-remote` starts the Remote-Control daemon.
    subprocess.run([*catraz, "-C", str(root), "run", "claude-remote"], env=env, check=True)
    yield root
    subprocess.run([*catraz, "-C", str(root), "stop"], env=env, check=False)


# ── T1 — live stack: /workspace/.catraz is empty inside container ─────────────


@pytest.mark.slow
def test_t1_workspace_catraz_empty(live_stack: Path) -> None:
    """Inside the running agent container .catraz is the empty tmpfs shadow."""
    r = _run(
        [
            "docker",
            "compose",
            "-f",
            str(live_stack / ".catraz" / "assets" / "compose" / "docker-compose.yml"),
            "--project-directory",
            str(live_stack),
            "exec",
            "-T",
            "claude-dev-env",
            "sh",
            "-c",
            "ls -A /workspace/.catraz | wc -l",
        ]
    )
    assert r.returncode == 0
    assert r.stdout.strip() == "0"


# ── T3 — tmpfs write does NOT propagate to host .catraz ──────────────────────


@pytest.mark.slow
def test_t3_tmpfs_write_isolation(live_stack: Path) -> None:
    """Writing into the tmpfs shadow must NOT appear on the host."""
    _run(
        [
            "docker",
            "compose",
            "-f",
            str(live_stack / ".catraz" / "assets" / "compose" / "docker-compose.yml"),
            "--project-directory",
            str(live_stack),
            "exec",
            "-T",
            "claude-dev-env",
            "sh",
            "-c",
            "echo EXFIL > /workspace/.catraz/exfil.txt",
        ]
    )
    assert not (live_stack / ".catraz" / "exfil.txt").exists()


# ── T4 — dev user cannot umount the tmpfs shadow ─────────────────────────────


@pytest.mark.slow
def test_t4_umount_eperm(live_stack: Path) -> None:
    """Unprivileged dev user must get EPERM when trying to umount the shadow."""
    r = _run(
        [
            "docker",
            "compose",
            "-f",
            str(live_stack / ".catraz" / "assets" / "compose" / "docker-compose.yml"),
            "--project-directory",
            str(live_stack),
            "exec",
            "-T",
            "--user",
            "dev",
            "claude-dev-env",
            "sh",
            "-c",
            "umount /workspace/.catraz 2>&1; echo rc=$?",
        ]
    )
    assert "rc=0" not in r.stdout  # must fail (EPERM or EACCES)


# ── T7a — in-container symlink stays in container namespace ──────────────────
# No @slow: uses only `docker run alpine`, no catraz stack.


def test_t7a_container_symlink_no_host_escape(tmp_path: Path) -> None:
    """A symlink created inside the container resolves within the container namespace
    and must not reveal the host path to the host-side .catraz secret."""
    (tmp_path / ".catraz").mkdir()
    (tmp_path / ".catraz/secret").write_text("HOST-SECRET")
    # Create a symlink INSIDE the tmpfs shadow pointing to parent — should resolve
    # to the container-local empty tmpfs, not the host bind mount parent.
    r = _run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{tmp_path}:/workspace",
            "--tmpfs",
            "/workspace/.catraz",
            "alpine",
            "sh",
            "-c",
            "ln -s /workspace/.catraz /tmp/link && ls -A /tmp/link | wc -l",
        ]
    )
    assert r.returncode == 0
    assert r.stdout.strip() == "0"  # symlink in container sees empty tmpfs


# ── T8 — /proc/self/mountinfo shows no reachable secret path ─────────────────
# No @slow: uses only `docker run alpine`, no catraz stack.


def test_t8_mountinfo_no_secret_path(tmp_path: Path) -> None:
    """/proc/self/mountinfo inside the container must not expose a device path
    that references the host .catraz directory directly."""
    (tmp_path / ".catraz").mkdir()
    (tmp_path / ".catraz/secret").write_text("HOST-SECRET")
    r = _run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{tmp_path}:/workspace",
            "--tmpfs",
            "/workspace/.catraz",
            "alpine",
            "sh",
            "-c",
            "grep '/workspace/.catraz' /proc/self/mountinfo | grep -v 'tmpfs' | wc -l",
        ]
    )
    assert r.returncode == 0
    assert r.stdout.strip() == "0"  # no non-tmpfs entry for /workspace/.catraz
