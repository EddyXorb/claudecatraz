"""Tests for P1: catraz allow + GitLab-remote auto-discovery in the wizard."""
import shutil
import subprocess
import types

import pytest

from catraz import policy
from catraz.commands import setup
from catraz.ui import Out
from catraz.errors import EXIT_OK, EXIT_CONFIG, CliError


def _out():
    return Out(color=False)


# ── _project_from_remote_url ──────────────────────────────────────────────────

def test_remote_https_with_git_suffix():
    assert policy._project_from_remote_url("https://gitlab.com/grp/proj.git") == "grp/proj"


def test_remote_https_without_git_suffix():
    assert policy._project_from_remote_url("https://gitlab.com/grp/proj") == "grp/proj"


def test_remote_ssh_scp_form():
    assert policy._project_from_remote_url("git@gitlab.com:grp/proj.git") == "grp/proj"


def test_remote_nested_path():
    assert policy._project_from_remote_url("https://gitlab.com/grp/sub/proj.git") == "grp/sub/proj"


def test_remote_non_matching_host():
    assert policy._project_from_remote_url("https://github.com/grp/proj.git") is None


def test_remote_self_hosted_host_match():
    url = "https://gitlab.example.com/grp/proj.git"
    assert policy._project_from_remote_url(url, "https://gitlab.example.com") == "grp/proj"
    # host-only compare ignores port
    assert policy._project_from_remote_url(url, "gitlab.example.com:8443") == "grp/proj"


def test_remote_invalid_path_returns_none():
    # a bare leaf name (no slash) fails validate_project → None
    assert policy._project_from_remote_url("git@gitlab.com:proj.git") is None


def test_remote_empty_or_garbage():
    assert policy._project_from_remote_url("") is None
    assert policy._project_from_remote_url("not a url") is None


# ── merge_allowed ─────────────────────────────────────────────────────────────

def test_merge_allowed_drops_empty_string():
    assert policy.merge_allowed([""], ["grp/proj"]) == ["grp/proj"]


def test_merge_allowed_dedupes_preserving_order():
    assert policy.merge_allowed(["a/b"], ["a/b", "c/d", "c/d"]) == ["a/b", "c/d"]


# ── cmd_allow ─────────────────────────────────────────────────────────────────

def _seed(tmp_path, allowed_line="allowed_projects    = []"):
    cfg = tmp_path / ".catraz" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "warden.toml").write_text(f"# warden\n{allowed_line}\n")
    (tmp_path / ".catraz" / ".env").write_text("AUTH_MODE=api_key\n")
    return cfg / "warden.toml"


def _ns(projects):
    return types.SimpleNamespace(projects=projects)


def test_cmd_allow_appends(tmp_path):
    warden = _seed(tmp_path)
    rc = setup.cmd_allow(tmp_path, _ns(["grp/proj"]), _out())
    assert rc == EXIT_OK
    assert policy._read_toml_allowed_projects(warden) == ["grp/proj"]


def test_cmd_allow_defensive_empty_string_default(tmp_path):
    warden = _seed(tmp_path, allowed_line='allowed_projects    = [""]')
    rc = setup.cmd_allow(tmp_path, _ns(["grp/proj"]), _out())
    assert rc == EXIT_OK
    assert policy._read_toml_allowed_projects(warden) == ["grp/proj"]


def test_cmd_allow_rejects_wildcard_writes_valid(tmp_path):
    warden = _seed(tmp_path)
    rc = setup.cmd_allow(tmp_path, _ns(["grp/*", "grp/ok"]), _out())
    assert rc == EXIT_OK
    assert policy._read_toml_allowed_projects(warden) == ["grp/ok"]


def test_cmd_allow_all_invalid_is_config_error(tmp_path):
    _seed(tmp_path)
    rc = setup.cmd_allow(tmp_path, _ns(["leaf", "grp/*"]), _out())
    assert rc == EXIT_CONFIG


def test_cmd_allow_idempotent(tmp_path):
    _seed(tmp_path)
    setup.cmd_allow(tmp_path, _ns(["grp/proj"]), _out())
    rc = setup.cmd_allow(tmp_path, _ns(["grp/proj"]), _out())
    assert rc == EXIT_OK  # "already allowed — nothing to add"


def test_cmd_allow_not_set_up(tmp_path):
    with pytest.raises(CliError):
        setup.cmd_allow(tmp_path, _ns(["grp/proj"]), _out())


def test_cmd_allow_warns_on_env_override(tmp_path, monkeypatch):
    warden = _seed(tmp_path)
    monkeypatch.setenv("WARDEN_ALLOWED_PROJECTS", "other/proj")
    msgs = []
    out = _out()
    monkeypatch.setattr(out, "warn", lambda m: msgs.append(m))
    rc = setup.cmd_allow(tmp_path, _ns(["grp/proj"]), out)
    assert rc == EXIT_OK
    assert any("WARDEN_ALLOWED_PROJECTS" in m for m in msgs)
    # the toml is still written even though the override shadows it
    assert policy._read_toml_allowed_projects(warden) == ["grp/proj"]


# ── _discover_gitlab_projects ─────────────────────────────────────────────────

@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_discover_gitlab_projects(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add",
                    "origin", "https://gitlab.com/grp/proj.git"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add",
                    "other", "https://github.com/grp/other.git"], check=True)
    assert policy._discover_gitlab_projects(tmp_path, "https://gitlab.com") == ["grp/proj"]
