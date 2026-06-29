"""configure_git_warden: every canonical GitLab remote form is routed to the warden.

These run against a real temp HOME so `git config --global` actually writes the
multivar insteadOf entries; we then read them back with `git config --get-all`.
"""
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

WARDEN = "http://gitlab-warden:8080/git/"
KEY = f"url.{WARDEN}.insteadOf"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _insteadof_values(home: Path) -> list[str]:
    r = subprocess.run(
        ["git", "config", "--global", "--get-all", KEY],
        env={"HOME": str(home)}, capture_output=True, text=True,
    )
    return r.stdout.split() if r.returncode == 0 else []


def _run(ep: Any, home: Path, monkeypatch: pytest.MonkeyPatch, **env: str) -> list[str]:
    monkeypatch.setenv("HOME", str(home))
    for k in ("GITLAB_MODE", "GITLAB_URL", "GITLAB_SSH_USER", "WARDEN_GIT_URL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    ep.configure_git_warden()
    return _insteadof_values(home)


def test_all_three_remote_forms_routed(ep: Any, tmp_path: Path,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    values = _run(ep, tmp_path, monkeypatch)
    assert set(values) == {
        "https://gitlab.com/",
        "git@gitlab.com:",
        "ssh://git@gitlab.com/",
    }


def test_self_hosted_host_and_ssh_user(ep: Any, tmp_path: Path,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    values = _run(ep, tmp_path, monkeypatch,
                  GITLAB_URL="https://gitlab.example.com", GITLAB_SSH_USER="gituser")
    assert set(values) == {
        "https://gitlab.example.com/",
        "gituser@gitlab.example.com:",
        "ssh://gituser@gitlab.example.com/",
    }


def test_idempotent_on_rerun(ep: Any, tmp_path: Path,
                             monkeypatch: pytest.MonkeyPatch) -> None:
    _run(ep, tmp_path, monkeypatch)
    values = _run(ep, tmp_path, monkeypatch)  # second pass on the same ~/.gitconfig
    assert len(values) == 3
    assert ep.os.environ["GIT_TERMINAL_PROMPT"] == "0"


def test_off_mode_writes_nothing(ep: Any, tmp_path: Path,
                                 monkeypatch: pytest.MonkeyPatch) -> None:
    values = _run(ep, tmp_path, monkeypatch, GITLAB_MODE="off")
    assert values == []
    assert ep.os.environ["GIT_TERMINAL_PROMPT"] == "0"
