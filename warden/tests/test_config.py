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

from warden.core.config import Config, ConfigError, GitEndpoint, GitRules, HostCredentials
from warden.core.config_load import _host_slug, from_env

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


def test_git_base_strips_api_suffix():
    assert Config(api_url="https://gl.example/api/v4").git_base == "https://gl.example"


# --- from_env: happy path ------------------------------------------------------
def test_from_env_parses_and_derives_urls():
    cfg = from_env(
        {
            **_MIN,
            "ALLOWED_PROJECTS": "group/proj, group/two",
            "GITLAB_URL": "https://gl.example/",
            "MAX_OPEN_MRS": "3",
        },
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
    with pytest.raises(
        ConfigError, match="branch_prefixes.*branch_prefix|branch_prefix.*branch_prefixes"
    ):
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
    cfg = from_env({**_MIN, "BRANCH_PREFIX": "claude/,bot/"}, strict=True, toml_path=str(toml))
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
    assert cfg.branch_prefixes == ("test/",)  # env wins
    assert cfg.max_open_mrs == 1  # env wins
    assert cfg.max_open_branches == 3  # not overridden → toml
    assert cfg.allowed_projects == ("group/x", "group/y")


def test_empty_env_falls_back_to_toml(tmp_path):
    # docker-compose passes empty strings when the .env var is unset → use the file.
    toml = tmp_path / "warden.toml"
    toml.write_text(_TOML)
    cfg = from_env(
        {
            "GITLAB_READ_TOKEN": "r",
            "GITLAB_WRITE_TOKEN": "w",
            "BRANCH_PREFIX": "",
            "ALLOWED_PROJECTS": "",
        },
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


# --- allowed_hosts / host_allowed (§07 Punkt 8 design spike) -------------------
# NOT yet wired into any request path — see
# docs/design/architecture-generalization/08-multi-target.md. These tests cover
# only the Config-level primitive: parsing + the pure allow/deny predicate.


def test_host_allowed_empty_allowlist_allows_anything():
    """Default (no [git.urls] configured) ⇒ feature off, no behaviour change."""
    cfg = Config()
    assert cfg.allowed_hosts == frozenset()
    assert cfg.host_allowed("gitlab.com")
    assert cfg.host_allowed("anything.example")
    assert cfg.host_allowed("")


def test_host_allowed_nonempty_allowlist_is_default_deny():
    cfg = Config(host_order=("gitlab.com", "my-gitlab.de"))
    assert cfg.host_allowed("gitlab.com")
    assert cfg.host_allowed("my-gitlab.de")
    assert not cfg.host_allowed("evil.example")
    assert not cfg.host_allowed("")


def test_host_allowed_normalizes_case_port_and_trailing_dot():
    cfg = Config(host_order=("gitlab.com",))
    assert cfg.host_allowed("GitLab.com")
    assert cfg.host_allowed("gitlab.com:443")
    assert cfg.host_allowed("gitlab.com.")


def test_git_urls_hosts_absent_section_yields_empty_allowlist(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text('allowed_projects = ["group/proj"]\n')
    cfg = from_env(_MIN, strict=True, toml_path=str(toml))
    assert cfg.allowed_hosts == frozenset()
    assert cfg.host_allowed("anything.example")


def test_git_urls_hosts_parsed_from_toml(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text('[git.urls]\nhosts = ["gitlab.com", "My-Gitlab.DE"]\n')
    env = {
        **_MIN,
        "GITLAB_READ_TOKEN__MY_GITLAB_DE": "r2",
        "GITLAB_WRITE_TOKEN__MY_GITLAB_DE": "w2",
    }
    cfg = from_env(env, strict=True, toml_path=str(toml))
    assert cfg.allowed_hosts == frozenset({"gitlab.com", "my-gitlab.de"})  # normalised
    assert cfg.host_order == ("gitlab.com", "my-gitlab.de")  # order preserved


def test_git_urls_hosts_with_port_normalizes_like_an_incoming_host_header(tmp_path):
    """Regression: an allowlist entry with a port (or trailing dot) must be
    normalised the same way ``host_allowed``/``resolve_target_host`` normalise
    an incoming ``Host`` header — otherwise it can never match and the host
    is silently denied forever (two divergent normalisations bug)."""
    toml = tmp_path / "warden.toml"
    toml.write_text('[git.urls]\nhosts = ["gitlab.internal:8443"]\n')
    cfg = from_env(_MIN, strict=True, toml_path=str(toml))
    assert cfg.host_order == ("gitlab.internal",)
    assert cfg.allowed_hosts == frozenset({"gitlab.internal"})
    assert cfg.host_allowed("gitlab.internal:8443")
    assert cfg.host_allowed("GITLAB.INTERNAL")


def test_git_urls_hosts_wrong_shape_aborts_startup(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text('[git.urls]\nhosts = "gitlab.com"\n')
    with pytest.raises(ConfigError, match="git.urls.hosts"):
        from_env(_MIN, strict=True, toml_path=str(toml))


def test_git_section_wrong_shape_aborts_startup(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text("git = 1\n")
    with pytest.raises(ConfigError, match=r"\[git\]"):
        from_env(_MIN, strict=True, toml_path=str(toml))


def test_git_urls_section_wrong_shape_aborts_startup(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text("[git]\nurls = 1\n")
    with pytest.raises(ConfigError, match=r"\[git\.urls\]"):
        from_env(_MIN, strict=True, toml_path=str(toml))


# --- per-host credentials (§07 Punkt 8 follow-up, design spike section 3) ------


def test_host_slug_is_lowercase_nonalnum_to_underscore_then_uppercase():
    assert _host_slug("my-gitlab.de") == "MY_GITLAB_DE"
    assert _host_slug("gitlab.com") == "GITLAB_COM"
    assert _host_slug("GitLab.COM") == "GITLAB_COM"  # case-insensitive input


def test_single_listed_host_aliases_the_legacy_env_vars(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text('[git.urls]\nhosts = ["gitlab.com"]\n')
    cfg = from_env(_MIN, strict=True, toml_path=str(toml))
    assert cfg.host_credentials == {"gitlab.com": HostCredentials(read_token="r", write_token="w")}


def test_additional_host_reads_slugged_env_vars(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text('[git.urls]\nhosts = ["gitlab.com", "my-gitlab.de"]\n')
    env = {
        **_MIN,
        "GITLAB_READ_TOKEN__MY_GITLAB_DE": "r2",
        "GITLAB_WRITE_TOKEN__MY_GITLAB_DE": "w2",
    }
    cfg = from_env(env, strict=True, toml_path=str(toml))
    assert cfg.host_credentials["gitlab.com"] == HostCredentials(read_token="r", write_token="w")
    assert cfg.host_credentials["my-gitlab.de"] == HostCredentials(
        read_token="r2", write_token="w2"
    )


def test_additional_host_file_variant_env_vars(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text('[git.urls]\nhosts = ["gitlab.com", "my-gitlab.de"]\n')
    f_read = tmp_path / "r2"
    f_read.write_text("r2-from-file\n")
    env = {
        **_MIN,
        "GITLAB_READ_TOKEN__MY_GITLAB_DE_FILE": str(f_read),
        "GITLAB_WRITE_TOKEN__MY_GITLAB_DE": "w2",
    }
    cfg = from_env(env, strict=True, toml_path=str(toml))
    assert cfg.host_credentials["my-gitlab.de"] == HostCredentials(
        read_token="r2-from-file", write_token="w2"
    )


def test_additional_host_missing_read_token_aborts_startup(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text('[git.urls]\nhosts = ["gitlab.com", "my-gitlab.de"]\n')
    with pytest.raises(ConfigError, match="GITLAB_READ_TOKEN__MY_GITLAB_DE"):
        from_env(_MIN, strict=True, toml_path=str(toml))


def test_additional_host_missing_write_token_aborts_in_read_write_mode(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text('[git.urls]\nhosts = ["gitlab.com", "my-gitlab.de"]\n')
    env = {**_MIN, "GITLAB_READ_TOKEN__MY_GITLAB_DE": "r2"}
    with pytest.raises(ConfigError, match="GITLAB_WRITE_TOKEN__MY_GITLAB_DE"):
        from_env(env, strict=True, toml_path=str(toml))


def test_additional_host_write_token_not_required_in_read_only_mode(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text('[git.urls]\nhosts = ["gitlab.com", "my-gitlab.de"]\n')
    env = {**_MIN, "GITLAB_MODE": "read-only", "GITLAB_READ_TOKEN__MY_GITLAB_DE": "r2"}
    cfg = from_env(env, strict=True, toml_path=str(toml))
    assert cfg.host_credentials["my-gitlab.de"] == HostCredentials(read_token="r2", write_token="")


def test_host_slug_collision_aborts_startup(tmp_path):
    # "a.b.com" and "a-b.com" both slug to "A_B_COM" — the design spike's own
    # example of a collision that must be rejected fail-closed, not silently
    # mixed up.
    toml = tmp_path / "warden.toml"
    toml.write_text('[git.urls]\nhosts = ["a.b.com", "a-b.com", "gitlab.com"]\n')
    with pytest.raises(ConfigError, match="A_B_COM"):
        from_env(_MIN, strict=True, toml_path=str(toml))


def test_secret_file_empty_fails_validate(tmp_path):
    """(d) *_FILE → empty file → _validate raises the existing 'required' error."""
    f = tmp_path / "rt"
    f.write_text("")
    with pytest.raises(ConfigError, match="GITLAB_READ_TOKEN"):
        from_env(
            {**_MIN, "GITLAB_READ_TOKEN_FILE": str(f), "GITLAB_READ_TOKEN": ""},
            strict=True,
        )


# --- [git.rules] + [[git.endpoint]] schema -------------------------------------
# Endpoint taxonomy replacing [git.urls] hosts: parsing, the rules cascade, and
# per-host lookups. Not yet wired into any request path.


def test_git_endpoints_parsed_from_toml(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text(
        "[git.rules]\n"
        'branch_prefixes = ["claude/"]\n'
        "max_open_mrs = 5\n"
        "\n"
        "[[git.endpoint]]\n"
        'host = "gitlab.com"\n'
        'type = "gitlab"\n'
        'allowed_projects = ["acme/infra", "acme/app"]\n'
        "\n"
        "[[git.endpoint]]\n"
        'host = "my-gitlab.de"\n'
        'type = "gitlab"\n'
        'allowed_projects = ["acme/infra"]\n'
        'rules = { max_open_mrs = 20, branch_prefixes = ["claude/", "bot/"] }\n'
        "\n"
        "[[git.endpoint]]\n"
        'host = "personal-gitserver.it"\n'
        'type = "plain"\n'
        'allowed_projects = ["me/dotfiles"]\n'
    )
    cfg = from_env(_MIN, strict=True, toml_path=str(toml))
    assert cfg.git_rules == GitRules(branch_prefixes=("claude/",), max_open_mrs=5)
    assert len(cfg.git_endpoints) == 3

    gitlab_com = cfg.endpoint_for("gitlab.com")
    assert gitlab_com is not None
    assert gitlab_com.type == "gitlab"
    assert gitlab_com.allowed_projects == ("acme/infra", "acme/app")
    assert gitlab_com.rules == GitRules()  # no override

    my_gitlab = cfg.endpoint_for("my-gitlab.de")
    assert my_gitlab is not None
    assert my_gitlab.rules == GitRules(max_open_mrs=20, branch_prefixes=("claude/", "bot/"))

    plain = cfg.endpoint_for("personal-gitserver.it")
    assert plain is not None
    assert plain.type == "plain"


def test_effective_rules_endpoint_override_wins_per_key():
    cfg = Config(
        git_rules=GitRules(max_open_mrs=5, max_open_branches=10, branch_prefixes=("claude/",)),
        git_endpoints=(
            GitEndpoint(
                host="my-gitlab.de",
                type="gitlab",
                rules=GitRules(max_open_mrs=20, branch_prefixes=("claude/", "bot/")),
            ),
        ),
    )
    rules = cfg.effective_rules("my-gitlab.de")
    assert rules.max_open_mrs == 20  # endpoint override wins
    assert rules.branch_prefixes == ("claude/", "bot/")  # list replaced, not merged
    assert rules.max_open_branches == 10  # no override → domain default


def test_effective_rules_falls_back_through_domain_to_builtin_default():
    cfg = Config(git_rules=GitRules(max_open_mrs=7))
    rules = cfg.effective_rules("unconfigured.example")  # no matching endpoint at all
    assert rules.max_open_mrs == 7  # domain default
    assert rules.max_open_branches == 10  # built-in default
    assert rules.branch_prefixes == ("claude/",)  # built-in default
    assert rules.max_writes_per_hour == 60  # built-in default
    assert rules.max_push_bytes == 50 * 1024 * 1024  # built-in default


def test_endpoint_for_and_git_allowed_hosts_are_normalised():
    cfg = Config(
        git_endpoints=(
            GitEndpoint(host="gitlab.com", type="gitlab"),
            GitEndpoint(host="my-gitlab.de", type="gitlab"),
        )
    )
    assert cfg.git_allowed_hosts == frozenset({"gitlab.com", "my-gitlab.de"})
    assert cfg.endpoint_for("GitLab.com:443") is not None
    assert cfg.endpoint_for("evil.example") is None


def test_git_project_allowed_is_scoped_per_endpoint_not_global():
    """Two endpoints sharing a project path on different hosts stay separate —
    one endpoint's allowlist must never leak to another host."""
    cfg = Config(
        git_endpoints=(
            GitEndpoint(host="gitlab.com", type="gitlab", allowed_projects=("acme/infra",)),
            GitEndpoint(host="my-gitlab.de", type="gitlab", allowed_projects=()),
        )
    )
    assert cfg.git_project_allowed("gitlab.com", "acme/infra")
    assert not cfg.git_project_allowed("my-gitlab.de", "acme/infra")
    assert not cfg.git_project_allowed("unconfigured.example", "acme/infra")


def test_git_endpoint_duplicate_host_aborts_startup(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text(
        '[[git.endpoint]]\nhost = "gitlab.com"\ntype = "gitlab"\n'
        '[[git.endpoint]]\nhost = "GitLab.com"\ntype = "gitlab"\n'
    )
    with pytest.raises(ConfigError, match="duplicate host"):
        from_env(_MIN, strict=True, toml_path=str(toml))


def test_git_endpoint_unknown_type_aborts_startup(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text('[[git.endpoint]]\nhost = "gitlab.com"\ntype = "bitbucket"\n')
    with pytest.raises(ConfigError, match="unknown type"):
        from_env(_MIN, strict=True, toml_path=str(toml))


def test_git_endpoint_github_type_is_reserved_not_implemented(tmp_path):
    """`type = "github"` is a recognised but not-yet-implemented type — a clear,
    distinct error, not silent acceptance of an unguarded forge."""
    toml = tmp_path / "warden.toml"
    toml.write_text('[[git.endpoint]]\nhost = "github.com"\ntype = "github"\n')
    with pytest.raises(ConfigError, match="not implemented"):
        from_env(_MIN, strict=True, toml_path=str(toml))


def test_git_rules_unknown_key_aborts_startup(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text("[git.rules]\nmax_open_mrz = 5\n")  # typo
    with pytest.raises(ConfigError, match="unknown key"):
        from_env(_MIN, strict=True, toml_path=str(toml))


def test_git_endpoint_rules_unknown_key_aborts_startup(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text(
        '[[git.endpoint]]\nhost = "gitlab.com"\ntype = "gitlab"\nrules = { bogus_key = 1 }\n'
    )
    with pytest.raises(ConfigError, match="unknown key"):
        from_env(_MIN, strict=True, toml_path=str(toml))


def test_git_endpoint_missing_host_aborts_startup(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text('[[git.endpoint]]\ntype = "gitlab"\n')
    with pytest.raises(ConfigError, match="host"):
        from_env(_MIN, strict=True, toml_path=str(toml))
