"""Tests for P1: catraz allow + GitLab-remote auto-discovery in the wizard."""

import argparse
import shutil
import subprocess
import types
from pathlib import Path
from typing import cast

import pytest

from catraz import policy
from catraz.commands import setup
from catraz.ui import Out
from catraz.errors import EXIT_OK, EXIT_CONFIG, CliError


def _out() -> Out:
    return Out(color=False)


# ── _project_from_remote_url ──────────────────────────────────────────────────


def test_remote_https_with_git_suffix() -> None:
    assert policy._project_from_remote_url("https://gitlab.com/grp/proj.git") == "grp/proj"


def test_remote_https_without_git_suffix() -> None:
    assert policy._project_from_remote_url("https://gitlab.com/grp/proj") == "grp/proj"


def test_remote_ssh_scp_form() -> None:
    assert policy._project_from_remote_url("git@gitlab.com:grp/proj.git") == "grp/proj"


def test_remote_nested_path() -> None:
    assert policy._project_from_remote_url("https://gitlab.com/grp/sub/proj.git") == "grp/sub/proj"


def test_remote_non_matching_host() -> None:
    assert policy._project_from_remote_url("https://github.com/grp/proj.git") is None


def test_remote_self_hosted_host_match() -> None:
    url = "https://gitlab.example.com/grp/proj.git"
    assert policy._project_from_remote_url(url, "https://gitlab.example.com") == "grp/proj"
    # host-only compare ignores port
    assert policy._project_from_remote_url(url, "gitlab.example.com:8443") == "grp/proj"


def test_remote_invalid_path_returns_none() -> None:
    # a bare leaf name (no slash) fails validate_project → None
    assert policy._project_from_remote_url("git@gitlab.com:proj.git") is None


def test_remote_empty_or_garbage() -> None:
    assert policy._project_from_remote_url("") is None
    assert policy._project_from_remote_url("not a url") is None


# ── merge_allowed ─────────────────────────────────────────────────────────────


def test_merge_allowed_drops_empty_string() -> None:
    assert policy.merge_allowed([""], ["grp/proj"]) == ["grp/proj"]


def test_merge_allowed_dedupes_preserving_order() -> None:
    assert policy.merge_allowed(["a/b"], ["a/b", "c/d", "c/d"]) == ["a/b", "c/d"]


# ── cmd_allow ─────────────────────────────────────────────────────────────────

_HOST = "gitlab.com"


def _seed(tmp_path: Path, projects: list[str] | None = None, *, with_endpoint: bool = True) -> Path:
    import json

    cfg = tmp_path / ".catraz" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    text = "# warden\n"
    if with_endpoint:
        proj_line = f"allowed_projects = {json.dumps(projects)}\n" if projects is not None else ""
        text += f'[[git.endpoint]]\nhost = "{_HOST}"\ntype = "gitlab"\n{proj_line}'
    (cfg / "warden.toml").write_text(text)
    (tmp_path / ".catraz" / ".env").write_text("AUTH_MODE=api_key\n")
    return cfg / "warden.toml"


def _ns(projects: list[str]) -> types.SimpleNamespace:
    return types.SimpleNamespace(projects=projects)


def test_cmd_allow_appends(tmp_path: Path) -> None:
    warden = _seed(tmp_path)
    rc = setup.cmd_allow(tmp_path, cast(argparse.Namespace, _ns(["grp/proj"])), _out())
    assert rc == EXIT_OK
    assert policy._read_toml_allowed_projects(warden, _HOST) == ["grp/proj"]


def test_cmd_allow_defensive_empty_string_default(tmp_path: Path) -> None:
    warden = _seed(tmp_path, projects=[""])
    rc = setup.cmd_allow(tmp_path, cast(argparse.Namespace, _ns(["grp/proj"])), _out())
    assert rc == EXIT_OK
    assert policy._read_toml_allowed_projects(warden, _HOST) == ["grp/proj"]


def test_cmd_allow_rejects_wildcard_writes_valid(tmp_path: Path) -> None:
    warden = _seed(tmp_path)
    rc = setup.cmd_allow(tmp_path, cast(argparse.Namespace, _ns(["grp/*", "grp/ok"])), _out())
    assert rc == EXIT_OK
    assert policy._read_toml_allowed_projects(warden, _HOST) == ["grp/ok"]


def test_cmd_allow_all_invalid_is_config_error(tmp_path: Path) -> None:
    _seed(tmp_path)
    rc = setup.cmd_allow(tmp_path, cast(argparse.Namespace, _ns(["leaf", "grp/*"])), _out())
    assert rc == EXIT_CONFIG


def test_cmd_allow_idempotent(tmp_path: Path) -> None:
    _seed(tmp_path)
    setup.cmd_allow(tmp_path, cast(argparse.Namespace, _ns(["grp/proj"])), _out())
    rc = setup.cmd_allow(tmp_path, cast(argparse.Namespace, _ns(["grp/proj"])), _out())
    assert rc == EXIT_OK  # "already allowed — nothing to add"


def test_cmd_allow_not_set_up(tmp_path: Path) -> None:
    with pytest.raises(CliError):
        setup.cmd_allow(tmp_path, cast(argparse.Namespace, _ns(["grp/proj"])), _out())


def test_cmd_allow_no_endpoint_configured(tmp_path: Path) -> None:
    """A warden.toml with no [[git.endpoint]] has nowhere to write
    allowed_projects — catraz allow refuses rather than guessing a host."""
    _seed(tmp_path, with_endpoint=False)
    rc = setup.cmd_allow(tmp_path, cast(argparse.Namespace, _ns(["grp/proj"])), _out())
    assert rc == EXIT_CONFIG


# ── _discover_gitlab_projects ─────────────────────────────────────────────────


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_discover_gitlab_projects(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "remote",
            "add",
            "origin",
            "https://gitlab.com/grp/proj.git",
        ],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "remote",
            "add",
            "other",
            "https://github.com/grp/other.git",
        ],
        check=True,
    )
    assert policy._discover_gitlab_projects(tmp_path, "https://gitlab.com") == ["grp/proj"]
