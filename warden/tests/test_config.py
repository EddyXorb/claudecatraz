"""config.py (W10): fail-closed env validation + allowlist matching.

The point of the module is to refuse to start when misconfigured rather than
run "open". These tests assert that refusal, plus the prefix-confusion guard in
``project_allowed`` (Q9).
"""

from __future__ import annotations

import pytest

from warden.config import Config, ConfigError, from_env

_MIN = {
    "ALLOWED_PROJECTS": "group/proj",
    "GITLAB_READ_TOKEN": "r",
    "GITLAB_WRITE_TOKEN": "w",
}


# --- project_allowed -----------------------------------------------------------
def test_project_allowed_exact_subpath_and_git_suffix():
    cfg = Config(allowed_projects=("group/proj",))
    assert cfg.project_allowed("group/proj")
    assert cfg.project_allowed("group/proj.git")  # .git stripped
    assert cfg.project_allowed("group/proj/sub")  # subpath
    assert cfg.project_allowed("/group/proj/")  # surrounding slashes ignored


def test_project_allowed_rejects_prefix_confusion():
    # "group/proj2" must NOT be allowed by an allowlist entry "group/proj".
    cfg = Config(allowed_projects=("group/proj",))
    assert not cfg.project_allowed("group/proj2")
    assert not cfg.project_allowed("other/secret")


def test_project_allowed_empty_allowlist_denies_all():
    assert not Config(allowed_projects=()).project_allowed("group/proj")


def test_project_allowed_matches_reconciled_numeric_id():
    # GitLab's /projects/:id accepts the numeric id, not just the path. Reconcile
    # fills allowed_project_ids; a request naming the id must pass, an unknown id
    # must still be denied (default-deny).
    cfg = Config(allowed_projects=("group/proj",), allowed_project_ids=("81882161",))
    assert cfg.project_allowed("81882161")
    assert not cfg.project_allowed("99999999")


def test_git_base_strips_api_suffix():
    assert Config(api_url="https://gl.example/api/v4").git_base == "https://gl.example"


# --- from_env: happy path ------------------------------------------------------
def test_from_env_parses_and_derives_urls():
    cfg = from_env(
        {**_MIN, "ALLOWED_PROJECTS": "group/proj, group/two", "GITLAB_URL": "https://gl.example/", "MAX_OPEN_MRS": "3"},
        strict=True,
    )
    assert cfg.allowed_projects == ("group/proj", "group/two")  # CSV split + trimmed
    assert cfg.api_url == "https://gl.example/api/v4"
    assert cfg.git_base == "https://gl.example"
    assert cfg.max_open_mrs == 3


def test_from_env_non_strict_allows_partial_config():
    cfg = from_env({}, strict=False)  # tests build partial configs this way
    assert cfg.allowed_projects == ()
    assert cfg.read_token == ""


# --- from_env: fail-closed validation -----------------------------------------
def test_missing_tokens_abort_startup():
    with pytest.raises(ConfigError) as exc:
        from_env({"ALLOWED_PROJECTS": "group/proj"}, strict=True)
    msg = str(exc.value)
    assert "GITLAB_READ_TOKEN" in msg and "GITLAB_WRITE_TOKEN" in msg


def test_empty_allowlist_aborts_startup():
    with pytest.raises(ConfigError, match="ALLOWED_PROJECTS"):
        from_env({"GITLAB_READ_TOKEN": "r", "GITLAB_WRITE_TOKEN": "w"}, strict=True)


def test_non_positive_quota_aborts_startup():
    with pytest.raises(ConfigError, match="MAX_OPEN_MRS"):
        from_env({**_MIN, "MAX_OPEN_MRS": "0"}, strict=True)


def test_non_integer_quota_aborts_startup():
    with pytest.raises(ConfigError, match="integer"):
        from_env({**_MIN, "MAX_OPEN_MRS": "abc"}, strict=True)


def test_empty_branch_prefix_aborts_startup(tmp_path):
    # An empty BRANCH_PREFIX env now means "fall back to the file", so an empty
    # prefix can only come from the toml — and must still abort.
    toml = tmp_path / "warden.toml"
    toml.write_text('branch_prefix = ""\n')
    with pytest.raises(ConfigError, match="BRANCH_PREFIX"):
        from_env(_MIN, strict=True, toml_path=str(toml))


# --- toml source of truth + env override (one source per setting) -------------
_TOML = (
    'branch_prefix = "claude/"\n'
    "max_open_mrs = 7\n"
    "max_open_branches = 3\n"
    "max_writes_per_hour = 99\n"
    'allowed_projects = ["group/a", "group/b"]\n'
)


def test_tunables_read_from_toml_when_env_absent(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text(_TOML)
    cfg = from_env({"GITLAB_READ_TOKEN": "r", "GITLAB_WRITE_TOKEN": "w"}, toml_path=str(toml))
    assert cfg.branch_prefix == "claude/"
    assert (cfg.max_open_mrs, cfg.max_open_branches, cfg.max_writes_per_hour) == (7, 3, 99)
    assert cfg.allowed_projects == ("group/a", "group/b")


def test_env_overrides_toml(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text(_TOML)
    cfg = from_env(
        {
            "GITLAB_READ_TOKEN": "r",
            "GITLAB_WRITE_TOKEN": "w",
            "BRANCH_PREFIX": "test/",
            "MAX_OPEN_MRS": "1",
            "ALLOWED_PROJECTS": "group/x,group/y",
        },
        toml_path=str(toml),
    )
    assert cfg.branch_prefix == "test/"            # env wins
    assert cfg.max_open_mrs == 1                    # env wins
    assert cfg.max_open_branches == 3              # not overridden → toml
    assert cfg.allowed_projects == ("group/x", "group/y")


def test_empty_env_falls_back_to_toml(tmp_path):
    # docker-compose passes empty strings when the .env var is unset → use the file.
    toml = tmp_path / "warden.toml"
    toml.write_text(_TOML)
    cfg = from_env(
        {"GITLAB_READ_TOKEN": "r", "GITLAB_WRITE_TOKEN": "w", "BRANCH_PREFIX": "", "ALLOWED_PROJECTS": ""},
        toml_path=str(toml),
    )
    assert cfg.branch_prefix == "claude/"
    assert cfg.allowed_projects == ("group/a", "group/b")


def test_missing_toml_uses_env_then_defaults(tmp_path):
    cfg = from_env(
        {"GITLAB_READ_TOKEN": "r", "GITLAB_WRITE_TOKEN": "w", "ALLOWED_PROJECTS": "group/x"},
        toml_path=str(tmp_path / "absent.toml"),
    )
    assert cfg.allowed_projects == ("group/x",)
    assert cfg.max_open_mrs == 5  # built-in default


def test_invalid_toml_type_aborts(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text('max_open_mrs = "lots"\n')
    with pytest.raises(ConfigError, match="integer"):
        from_env({}, strict=False, toml_path=str(toml))
