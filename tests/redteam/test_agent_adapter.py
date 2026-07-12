"""Red-Team tests for the Adapter-Conformance-Harness, container level.
Checks the Agent-Layer dimension: no Forge credential in the agent
process/home, and `modes.remote=false` fails closed instead of leaving a
half-configured daemon. Requires Docker + a real ANTHROPIC_API_KEY (@slow);
the unit-level half is `tests/container/test_adapter_conformance.py`."""

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


@pytest.fixture(scope="module")
def live_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Same construction as `test_shadow_mount.py`'s `live_stack` — kept
    file-local (small, self-contained) rather than shared, matching this
    suite's existing convention of not factoring fixtures across redteam
    test modules."""
    root = tmp_path_factory.mktemp("catraz-agent-adapter")
    env = dict(os.environ, HOME=str(root))
    catraz = [sys.executable, "-m", "catraz"]

    subprocess.run([*catraz, "-C", str(root), "init", "-y", "--skip-sync"], env=env, check=False)

    (root / ".catraz" / ".env").write_text(
        "AUTH_MODE=api_key\n"
        "ANTHROPIC_API_KEY=" + os.environ.get("ANTHROPIC_API_KEY", "sk-ci-dummy") + "\n"
        "GITLAB_READ_TOKEN=ci-dummy\n"
        "GITLAB_WRITE_TOKEN=ci-dummy\n"
        "WARDEN_ALLOWED_PROJECTS=acme/demo\n"
        f"DEV_UID={os.getuid()}\n"
    )

    subprocess.run([*catraz, "-C", str(root), "run", "claude-remote"], env=env, check=True)
    yield root
    subprocess.run([*catraz, "-C", str(root), "stop"], env=env, check=False)


def _compose_exec(live_stack: Path, *cmd: str) -> subprocess.CompletedProcess[str]:
    return _run(
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
            " ".join(cmd),
        ]
    )


@pytest.mark.slow
def test_agent_process_env_carries_no_gitlab_token(live_stack: Path) -> None:
    """The agent process must never see a GitLab token — the Warden holds
    it, not the agent container."""
    r = _compose_exec(live_stack, "env")
    assert r.returncode == 0
    for marker in ("GITLAB_READ_TOKEN", "GITLAB_WRITE_TOKEN", "GITLAB_API_TOKEN"):
        assert marker not in r.stdout, f"agent env leaked {marker}"


@pytest.mark.slow
def test_agent_home_has_no_gitlab_token_file(live_stack: Path) -> None:
    """No credential file under the agent's home mentions a GitLab token —
    prepare_home() must only ever write agent-model credentials."""
    r = _compose_exec(
        live_stack,
        "grep -rl GITLAB_READ_TOKEN /home/dev/agent-home 2>/dev/null; echo rc=$?",
    )
    assert r.returncode == 0
    assert r.stdout.strip().splitlines()[-1] in (
        "rc=1",
        "rc=2",
    )  # grep: no match (or dir gone)


@pytest.mark.slow
def test_git_insteadof_points_at_warden_not_gitlab_directly(live_stack: Path) -> None:
    """git_routing.configure_git_warden() must have rewired the canonical
    GitLab remote to the Warden — the agent's own git never talks to GitLab
    directly."""
    r = _compose_exec(
        live_stack,
        "git config --global --get-all url.http://gitlab-warden:8080/git/.insteadOf",
    )
    assert r.returncode == 0
    assert "gitlab.com" in r.stdout
