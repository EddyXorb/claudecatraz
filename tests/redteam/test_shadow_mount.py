"""Red-Team tests for Doc 03 — shadow mount.

T1–T4, T7, T8 require Docker; T7b (host-side symlink guard) is a pure unit
test that lives in tests/cli/test_invariants.py.

Run with Docker available:
    uv run --with pytest python -m pytest tests/redteam/ -q
"""
import shutil
import subprocess
import sys
import pytest


def _docker_available():
    if not shutil.which("docker"):
        return False
    r = subprocess.run(["docker", "info"], capture_output=True)
    return r.returncode == 0


pytestmark = pytest.mark.skipif(not _docker_available(), reason="needs docker")


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


# ── T2 — baseline: tmpfs ordering guarantee (gating test) ────────────────────

def test_t2_tmpfs_overdeck_ordering(tmp_path):
    """tmpfs over a bind subpath masks host content deterministically."""
    (tmp_path / ".catraz").mkdir()
    (tmp_path / ".catraz/secret").write_text("TOP")
    r = _run(["docker", "run", "--rm",
              "-v", f"{tmp_path}:/workspace",
              "--tmpfs", "/workspace/.catraz",
              "alpine", "sh", "-c", "ls -A /workspace/.catraz | wc -l"])
    assert r.returncode == 0
    assert r.stdout.strip() == "0"          # .catraz appears EMPTY to the container


# ── Fixture: running stack via catraz init -y + up ────────────────────────────

@pytest.fixture(scope="module")
def live_stack(tmp_path_factory):
    """Start a catraz stack in a temporary project dir; tear down after module."""
    import os
    root = tmp_path_factory.mktemp("catraz-proj")
    env = dict(os.environ, HOME=str(root))
    catraz = [sys.executable, "-m", "catraz"]

    subprocess.run([*catraz, "-C", str(root), "init", "-y", "--skip-sync"],
                   env=env, check=True)
    subprocess.run([*catraz, "-C", str(root), "up"], env=env, check=True)
    yield root
    subprocess.run([*catraz, "-C", str(root), "down"], env=env, check=False)


# ── T1 — live stack: /workspace/.catraz is empty inside container ─────────────

@pytest.mark.slow
def test_t1_workspace_catraz_empty(live_stack):
    """Inside the running agent container .catraz is the empty tmpfs shadow."""
    r = _run(["docker", "compose",
              "-f", str(live_stack / ".catraz" / "assets" / "compose" / "docker-compose.yml"),
              "--project-directory", str(live_stack),
              "exec", "-T", "claude-dev-env",
              "sh", "-c", "ls -A /workspace/.catraz | wc -l"])
    assert r.returncode == 0
    assert r.stdout.strip() == "0"


# ── T3 — tmpfs write does NOT propagate to host .catraz ──────────────────────

@pytest.mark.slow
def test_t3_tmpfs_write_isolation(live_stack):
    """Writing into the tmpfs shadow must NOT appear on the host."""
    _run(["docker", "compose",
          "-f", str(live_stack / ".catraz" / "assets" / "compose" / "docker-compose.yml"),
          "--project-directory", str(live_stack),
          "exec", "-T", "claude-dev-env",
          "sh", "-c", "echo EXFIL > /workspace/.catraz/exfil.txt"])
    assert not (live_stack / ".catraz" / "exfil.txt").exists()


# ── T4 — dev user cannot umount the tmpfs shadow ─────────────────────────────

@pytest.mark.slow
def test_t4_umount_eperm(live_stack):
    """Unprivileged dev user must get EPERM when trying to umount the shadow."""
    r = _run(["docker", "compose",
              "-f", str(live_stack / ".catraz" / "assets" / "compose" / "docker-compose.yml"),
              "--project-directory", str(live_stack),
              "exec", "-T", "--user", "dev", "claude-dev-env",
              "sh", "-c", "umount /workspace/.catraz 2>&1; echo rc=$?"])
    assert "rc=0" not in r.stdout  # must fail (EPERM or EACCES)
