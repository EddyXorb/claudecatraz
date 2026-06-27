"""Red-Team tests for Doc 03 — shadow mount.

T1–T4, T7, T8 require Docker; T7b (host-side symlink guard) is a pure unit
test that lives in tests/cli/test_invariants.py.

Run with Docker available:
    uv run --with pytest python -m pytest tests/redteam/ -q
"""
import shutil
import subprocess
import pytest


def _docker_available():
    if not shutil.which("docker"):
        return False
    r = subprocess.run(["docker", "info"], capture_output=True)
    return r.returncode == 0


pytestmark = pytest.mark.skipif(not _docker_available(), reason="needs docker")


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


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
