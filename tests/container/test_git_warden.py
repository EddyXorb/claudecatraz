"""configure_git_warden: every configured [[git.endpoint]] host gets its own
rewrite (https -> http://<host>:8080/git/), hostname preserved. The /git/
prefix is the warden's git-transport mount — a request without it matches no
route and 404s.

These run against a real temp HOME so `git config --global` actually writes the
multivar insteadOf entries; we then read them back with `git config --get-all`.
"""

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _write_warden_toml(path: Path, hosts: list[str]) -> None:
    lines = ["[git.rules]", ""]
    for host in hosts:
        lines.append("[[git.endpoint]]")
        lines.append(f'host = "{host}"')
        lines.append('type = "gitlab"')
        lines.append("")
    path.write_text("\n".join(lines))


def _insteadof_values(home: Path, host: str) -> list[str]:
    key = f"url.http://{host}:8080/git/.insteadOf"
    r = subprocess.run(
        ["git", "config", "--global", "--get-all", key],
        env={"HOME": str(home)},
        capture_output=True,
        text=True,
    )
    return r.stdout.split() if r.returncode == 0 else []


def _run(
    ep: Any,
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
    hosts: list[str],
    *,
    warden_toml: Path | None = None,
    **env: str,
) -> Path:
    """Write a warden.toml with the given hosts (unless one is passed
    explicitly), run configure_git_warden against it, return the toml path
    used (so callers can re-derive per-host insteadOf keys)."""
    monkeypatch.setenv("HOME", str(home))
    for k in ("WARDEN_GIT_URL",):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    toml_path = warden_toml or (home / "warden.toml")
    _write_warden_toml(toml_path, hosts)
    ep.configure_git_warden(toml_path)
    return toml_path


def test_single_host_all_three_remote_forms_routed(
    ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(ep, tmp_path, monkeypatch, ["gitlab.com"])
    assert set(_insteadof_values(tmp_path, "gitlab.com")) == {
        "https://gitlab.com/",
        "git@gitlab.com:",
        "ssh://git@gitlab.com/",
    }


def test_rewrite_keeps_canonical_hostname_with_git_mount(
    ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Target is exactly http://<host>:8080/git/ — same hostname, only
    scheme+port and the /git/ mount change, never a different hostname or the
    warden container name."""
    _run(ep, tmp_path, monkeypatch, ["my-gitlab.de"])
    r = subprocess.run(
        ["git", "config", "--global", "--get-regexp", r"^url\..*\.insteadof$"],
        env={"HOME": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert "gitlab-warden" not in r.stdout
    keys = {line.split()[0] for line in r.stdout.splitlines() if line.strip()}
    assert keys == {"url.http://my-gitlab.de:8080/git/.insteadof"}


def test_multi_host_each_gets_its_own_rewrite(
    ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(ep, tmp_path, monkeypatch, ["gitlab.com", "my-gitlab.de"])
    assert set(_insteadof_values(tmp_path, "gitlab.com")) == {
        "https://gitlab.com/",
        "git@gitlab.com:",
        "ssh://git@gitlab.com/",
    }
    assert set(_insteadof_values(tmp_path, "my-gitlab.de")) == {
        "https://my-gitlab.de/",
        "git@my-gitlab.de:",
        "ssh://git@my-gitlab.de/",
    }


def test_self_hosted_ssh_user_applies_to_every_host(
    ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(ep, tmp_path, monkeypatch, ["gitlab.example.com"], GITLAB_SSH_USER="gituser")
    assert set(_insteadof_values(tmp_path, "gitlab.example.com")) == {
        "https://gitlab.example.com/",
        "gituser@gitlab.example.com:",
        "ssh://gituser@gitlab.example.com/",
    }


def test_idempotent_on_rerun(ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    toml_path = tmp_path / "warden.toml"
    _run(ep, tmp_path, monkeypatch, ["gitlab.com"], warden_toml=toml_path)
    _run(ep, tmp_path, monkeypatch, ["gitlab.com"], warden_toml=toml_path)  # 2nd pass, same home
    values = _insteadof_values(tmp_path, "gitlab.com")
    assert len(values) == 3
    assert ep.os.environ["GIT_TERMINAL_PROMPT"] == "0"


def test_no_endpoints_writes_nothing(
    ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty [[git.endpoint]] list already routes nothing, no separate
    off-switch needed."""
    values = _run(ep, tmp_path, monkeypatch, [])
    assert _insteadof_values(tmp_path, "gitlab.com") == []
    assert ep.os.environ["GIT_TERMINAL_PROMPT"] == "0"
    assert values == tmp_path / "warden.toml"


def test_missing_warden_toml_writes_nothing(
    ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing/unreadable warden.toml degrades to "route nothing", same as
    an explicitly empty endpoint list — never a crash."""
    monkeypatch.setenv("HOME", str(tmp_path))
    ep.configure_git_warden(tmp_path / "does-not-exist.toml")
    assert _insteadof_values(tmp_path, "gitlab.com") == []
    assert ep.os.environ["GIT_TERMINAL_PROMPT"] == "0"


def test_malformed_toml_writes_nothing(
    ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    toml_path = tmp_path / "warden.toml"
    toml_path.write_text("not [ valid toml")
    ep.configure_git_warden(toml_path)
    assert _insteadof_values(tmp_path, "gitlab.com") == []
