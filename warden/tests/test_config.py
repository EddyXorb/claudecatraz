"""config.py (W10): fail-closed env validation + allowlist matching.

The point of the module is to refuse to start when misconfigured rather than
run "open". These tests assert that refusal, plus the prefix-confusion guard in
``project_allowed`` (Q9).

GITLAB_MODE introduces three operating modes:
  off        — no tokens or allowlist required; all GitLab ops denied.
  read-only  — read token + allowlist required; no write token.
  read-write — both tokens + allowlist required (default, previous behaviour).
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
def test_project_allowed_exact_match_and_git_suffix():
    cfg = Config(allowed_projects=("group/proj",))
    assert cfg.project_allowed("group/proj")
    assert cfg.project_allowed("group/proj.git")  # .git stripped
    assert cfg.project_allowed("/group/proj/")  # surrounding slashes ignored


def test_project_allowed_rejects_prefix_confusion():
    # "group/proj2" must NOT be allowed by an allowlist entry "group/proj".
    cfg = Config(allowed_projects=("group/proj",))
    assert not cfg.project_allowed("group/proj2")
    assert not cfg.project_allowed("other/secret")


def test_project_allowed_rejects_subpath():
    # B4: the allowlist names concrete projects, never group/project prefixes —
    # "group/proj/sub" must NOT be allowed by an allowlist entry "group/proj".
    cfg = Config(allowed_projects=("group/proj",))
    assert not cfg.project_allowed("group/proj/sub")


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


# --- from_env: fail-closed validation (read-write mode, the default) ----------
def test_missing_tokens_abort_startup():
    """In GITLAB_MODE=read-write (default) both tokens are required."""
    with pytest.raises(ConfigError) as exc:
        from_env({"ALLOWED_PROJECTS": "group/proj"}, strict=True)
    msg = str(exc.value)
    assert "GITLAB_READ_TOKEN" in msg and "GITLAB_WRITE_TOKEN" in msg


def test_empty_allowlist_boots_and_denies():
    """In GITLAB_MODE=read-write an empty allowlist does NOT abort: the warden
    boots (dev-env runs offline) and project_allowed() denies every project."""
    cfg = from_env({"GITLAB_READ_TOKEN": "r", "GITLAB_WRITE_TOKEN": "w"}, strict=True)
    assert cfg.allowed_projects == ()
    assert not cfg.project_allowed("group/proj")


# --- GITLAB_MODE=off: no token or allowlist required --------------------------
def test_off_mode_allows_empty_tokens_and_allowlist():
    """GITLAB_MODE=off is valid with no tokens and no allowlist — GitLab is intentionally off."""
    cfg = from_env({"GITLAB_MODE": "off"}, strict=True)
    assert cfg.gitlab_mode == "off"
    assert not cfg.gitlab_enabled
    assert not cfg.writes_enabled


def test_off_mode_default_from_env():
    """GITLAB_MODE=off builds correctly and exposes the right flags."""
    cfg = from_env({"GITLAB_MODE": "off"}, strict=True)
    assert cfg.gitlab_enabled is False
    assert cfg.writes_enabled is False


# --- GITLAB_MODE=read-only: read token + allowlist required, no write token ---
def test_read_only_requires_read_token_not_write():
    """GITLAB_MODE=read-only: read token + allowlist required; write token NOT required."""
    cfg = from_env(
        {"GITLAB_MODE": "read-only", "GITLAB_READ_TOKEN": "r", "ALLOWED_PROJECTS": "group/proj"},
        strict=True,
    )
    assert cfg.gitlab_mode == "read-only"
    assert cfg.gitlab_enabled
    assert not cfg.writes_enabled


def test_read_only_with_write_token_is_fine():
    """GITLAB_MODE=read-only: a write token present is ignored (no requirement, no error)."""
    cfg = from_env(
        {
            "GITLAB_MODE": "read-only",
            "GITLAB_READ_TOKEN": "r",
            "GITLAB_WRITE_TOKEN": "w",
            "ALLOWED_PROJECTS": "group/proj",
        },
        strict=True,
    )
    assert cfg.gitlab_mode == "read-only"
    assert not cfg.writes_enabled


def test_read_only_missing_read_token_aborts():
    """GITLAB_MODE=read-only: missing read token still aborts."""
    with pytest.raises(ConfigError, match="GITLAB_READ_TOKEN"):
        from_env({"GITLAB_MODE": "read-only", "ALLOWED_PROJECTS": "group/proj"}, strict=True)


def test_read_only_empty_allowlist_boots_and_denies():
    """GITLAB_MODE=read-only: empty allowlist boots (deny-all), not abort."""
    cfg = from_env({"GITLAB_MODE": "read-only", "GITLAB_READ_TOKEN": "r"}, strict=True)
    assert cfg.allowed_projects == ()
    assert not cfg.project_allowed("group/proj")


# --- Invalid mode aborts -------------------------------------------------------
def test_invalid_mode_aborts():
    """An unrecognised GITLAB_MODE value aborts with a clear error."""
    with pytest.raises(ConfigError, match="GITLAB_MODE"):
        from_env({"GITLAB_MODE": "nonsense"}, strict=True)


# --- Properties on Config dataclass -------------------------------------------
def test_config_properties_read_write():
    cfg = Config(gitlab_mode="read-write")
    assert cfg.gitlab_enabled is True
    assert cfg.writes_enabled is True


def test_config_properties_read_only():
    cfg = Config(gitlab_mode="read-only")
    assert cfg.gitlab_enabled is True
    assert cfg.writes_enabled is False


def test_config_properties_off():
    cfg = Config(gitlab_mode="off")
    assert cfg.gitlab_enabled is False
    assert cfg.writes_enabled is False


def test_non_positive_quota_aborts_startup():
    with pytest.raises(ConfigError, match="MAX_OPEN_MRS"):
        from_env({**_MIN, "MAX_OPEN_MRS": "0"}, strict=True)


def test_non_integer_quota_aborts_startup():
    with pytest.raises(ConfigError, match="integer"):
        from_env({**_MIN, "MAX_OPEN_MRS": "abc"}, strict=True)


def test_empty_branch_prefix_aborts_startup(tmp_path):
    # An empty BRANCH_PREFIX env now means "fall back to the file", so an empty
    # prefix can only come from the toml (legacy scalar form) — and must still abort.
    toml = tmp_path / "warden.toml"
    toml.write_text('branch_prefix = ""\n')
    with pytest.raises(ConfigError, match="BRANCH_PREFIX"):
        from_env(_MIN, strict=True, toml_path=str(toml))


def test_empty_branch_prefixes_list_aborts_startup(tmp_path):
    """An empty ``branch_prefixes`` list is fail-closed: it must not mean "no filter"."""
    toml = tmp_path / "warden.toml"
    toml.write_text("branch_prefixes = []\n")
    with pytest.raises(ConfigError, match="BRANCH_PREFIX"):
        from_env(_MIN, strict=True, toml_path=str(toml))


def test_branch_prefixes_list_with_empty_element_aborts_startup(tmp_path):
    """A blank element (e.g. ``["claude/", ""]``) would allow every branch — reject it."""
    toml = tmp_path / "warden.toml"
    toml.write_text('branch_prefixes = ["claude/", ""]\n')
    with pytest.raises(ConfigError, match="BRANCH_PREFIX"):
        from_env(_MIN, strict=True, toml_path=str(toml))


def test_branch_prefixes_and_legacy_branch_prefix_both_set_aborts(tmp_path):
    """Two sources of truth for the same namespace (list + legacy scalar) is an error."""
    toml = tmp_path / "warden.toml"
    toml.write_text('branch_prefixes = ["claude/"]\nbranch_prefix = "claude/"\n')
    with pytest.raises(ConfigError, match="branch_prefixes.*branch_prefix|branch_prefix.*branch_prefixes"):
        from_env(_MIN, strict=True, toml_path=str(toml))


def test_branch_prefixes_list_from_toml(tmp_path):
    """A ``branch_prefixes`` list in warden.toml becomes the tuple as-is."""
    toml = tmp_path / "warden.toml"
    toml.write_text('branch_prefixes = ["claude/", "bot/"]\n')
    cfg = from_env(_MIN, strict=True, toml_path=str(toml))
    assert cfg.branch_prefixes == ("claude/", "bot/")


def test_legacy_branch_prefix_scalar_becomes_single_element_tuple(tmp_path):
    """The legacy scalar ``branch_prefix = "..."`` form stays valid as a 1-element list."""
    toml = tmp_path / "warden.toml"
    toml.write_text('branch_prefix = "claude/"\n')
    cfg = from_env(_MIN, strict=True, toml_path=str(toml))
    assert cfg.branch_prefixes == ("claude/",)


def test_branch_prefix_env_csv_overrides_toml_list(tmp_path):
    """BRANCH_PREFIX env accepts CSV for multiple prefixes, and wins over the file."""
    toml = tmp_path / "warden.toml"
    toml.write_text('branch_prefixes = ["claude/"]\n')
    cfg = from_env(
        {**_MIN, "BRANCH_PREFIX": "claude/,bot/"}, strict=True, toml_path=str(toml)
    )
    assert cfg.branch_prefixes == ("claude/", "bot/")


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
    assert cfg.branch_prefixes == ("claude/",)
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
    assert cfg.branch_prefixes == ("test/",)        # env wins
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
    assert cfg.branch_prefixes == ("claude/",)
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


# --- _secret / *_FILE indirection (11.1) --------------------------------------

def test_secret_file_read_token(tmp_path):
    """(a) GITLAB_READ_TOKEN_FILE → tmp file "glpat-x\n" → read_token == "glpat-x"."""
    f = tmp_path / "rt"
    f.write_text("glpat-x\n")
    cfg = from_env(
        {**_MIN, "GITLAB_READ_TOKEN_FILE": str(f), "GITLAB_READ_TOKEN": ""},
        strict=True,
    )
    assert cfg.read_token == "glpat-x"


def test_secret_file_wins_over_env(tmp_path):
    """(b) *_FILE and the bare env var both set → the file wins."""
    f = tmp_path / "rt"
    f.write_text("from-file\n")
    cfg = from_env(
        {**_MIN, "GITLAB_READ_TOKEN_FILE": str(f), "GITLAB_READ_TOKEN": "from-env"},
        strict=True,
    )
    assert cfg.read_token == "from-file"


def test_secret_file_missing_raises(tmp_path):
    """(c) *_FILE → missing path → ConfigError."""
    with pytest.raises(ConfigError, match="GITLAB_READ_TOKEN_FILE"):
        from_env(
            {**_MIN, "GITLAB_READ_TOKEN_FILE": str(tmp_path / "nonexistent")},
            strict=True,
        )


def test_secret_file_empty_fails_validate(tmp_path):
    """(d) *_FILE → empty file → _validate raises the existing 'required' error."""
    f = tmp_path / "rt"
    f.write_text("")
    with pytest.raises(ConfigError, match="GITLAB_READ_TOKEN"):
        from_env(
            {**_MIN, "GITLAB_READ_TOKEN_FILE": str(f), "GITLAB_READ_TOKEN": ""},
            strict=True,
        )
