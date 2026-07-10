"""config.py: fail-closed env validation + allowlist matching.

Asserts refusal to start when misconfigured, plus the prefix-confusion
guard in project_allowed. Policy tunables come from warden.toml only;
access is derived per host from which tokens are present."""

from __future__ import annotations

import pytest

from warden.core.config import Config, ConfigError, GitEndpoint, GitRules, HostCredentials
from warden.core.config_load import _parse_token_file, from_env
from warden.guards.git.actions import DEFAULT as GIT_DEFAULT


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


# --- from_env: happy path -------------------------------------------------------
def test_from_env_parses_projects_and_quota_from_toml(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text('allowed_projects = ["group/proj", "group/two"]\nmax_open_mrs = 3\n')
    cfg = from_env({}, strict=True, toml_path=str(toml))
    assert cfg.allowed_projects == ("group/proj", "group/two")
    assert cfg.max_open_mrs == 3


def test_from_env_non_strict_allows_partial_config():
    cfg = from_env({}, strict=False)  # tests build partial configs this way
    assert cfg.allowed_projects == ()
    assert cfg.git_endpoints == ()


# --- from_env: fail-closed validation — no token/allowlist requirement; only
# quota/branch-namespace sanity checks can abort startup. ---


def test_empty_config_boots_without_aborting():
    """No endpoints, no tokens, no allowlist — the warden boots (fail-closed
    *degrade*, not fail-stop) and denies every git operation via `host_gate`."""
    cfg = from_env({}, strict=True)
    assert cfg.git_endpoints == ()
    assert cfg.allowed_projects == ()
    assert not cfg.host_allowed("gitlab.com")


def test_empty_allowlist_boots_and_denies():
    """An empty allowlist does NOT abort: the warden boots (dev-env runs
    offline) and project_allowed() denies every project."""
    cfg = from_env({}, strict=True)
    assert cfg.allowed_projects == ()
    assert not cfg.project_allowed("group/proj")


def test_non_positive_quota_aborts_startup(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text("max_open_mrs = 0\n")
    with pytest.raises(ConfigError, match="MAX_OPEN_MRS"):
        from_env({}, strict=True, toml_path=str(toml))


def test_non_integer_quota_aborts_startup(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text('max_open_mrs = "abc"\n')
    with pytest.raises(ConfigError, match="integer"):
        from_env({}, strict=True, toml_path=str(toml))


def test_empty_branch_prefix_aborts_startup(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text('branch_prefix = ""\n')
    with pytest.raises(ConfigError, match="BRANCH_PREFIX"):
        from_env({}, strict=True, toml_path=str(toml))


def test_empty_branch_prefixes_list_aborts_startup(tmp_path):
    """An empty branch_prefixes list is fail-closed: it must not mean "no filter"."""
    toml = tmp_path / "warden.toml"
    toml.write_text("branch_prefixes = []\n")
    with pytest.raises(ConfigError, match="BRANCH_PREFIX"):
        from_env({}, strict=True, toml_path=str(toml))


def test_branch_prefixes_list_with_empty_element_aborts_startup(tmp_path):
    """A blank element (e.g. ["claude/", ""]) would allow every branch — reject it."""
    toml = tmp_path / "warden.toml"
    toml.write_text('branch_prefixes = ["claude/", ""]\n')
    with pytest.raises(ConfigError, match="BRANCH_PREFIX"):
        from_env({}, strict=True, toml_path=str(toml))


def test_branch_prefixes_and_legacy_branch_prefix_both_set_aborts(tmp_path):
    """Two sources of truth for the same namespace (list + legacy scalar) is an error."""
    toml = tmp_path / "warden.toml"
    toml.write_text('branch_prefixes = ["claude/"]\nbranch_prefix = "claude/"\n')
    with pytest.raises(
        ConfigError, match="branch_prefixes.*branch_prefix|branch_prefix.*branch_prefixes"
    ):
        from_env({}, strict=True, toml_path=str(toml))


def test_branch_prefixes_list_from_toml(tmp_path):
    """A branch_prefixes list in warden.toml becomes the tuple as-is."""
    toml = tmp_path / "warden.toml"
    toml.write_text('branch_prefixes = ["claude/", "bot/"]\n')
    cfg = from_env({}, strict=True, toml_path=str(toml))
    assert cfg.branch_prefixes == ("claude/", "bot/")


def test_legacy_branch_prefix_scalar_becomes_single_element_tuple(tmp_path):
    """The legacy scalar branch_prefix = "..." form stays valid as a 1-element list."""
    toml = tmp_path / "warden.toml"
    toml.write_text('branch_prefix = "claude/"\n')
    cfg = from_env({}, strict=True, toml_path=str(toml))
    assert cfg.branch_prefixes == ("claude/",)


def test_branch_prefix_env_has_no_effect(tmp_path):
    """BRANCH_PREFIX has no effect — only warden.toml sets the branch
    namespace; a set env var is silently ignored, not applied."""
    toml = tmp_path / "warden.toml"
    toml.write_text('branch_prefixes = ["claude/"]\n')
    cfg = from_env({"BRANCH_PREFIX": "test/,bot/"}, strict=True, toml_path=str(toml))
    assert cfg.branch_prefixes == ("claude/",)


# --- toml is the only source for policy tunables --------------------------------
_TOML = (
    'branch_prefix = "claude/"\n'
    "max_open_mrs = 7\n"
    "max_open_branches = 3\n"
    "max_writes_per_hour = 99\n"
    'allowed_projects = ["group/a", "group/b"]\n'
)


def test_tunables_read_from_toml(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text(_TOML)
    cfg = from_env({}, toml_path=str(toml))
    assert cfg.branch_prefixes == ("claude/",)
    assert (cfg.max_open_mrs, cfg.max_open_branches, cfg.max_writes_per_hour) == (7, 3, 99)
    assert cfg.allowed_projects == ("group/a", "group/b")


def test_policy_env_vars_have_no_effect(tmp_path):
    """BRANCH_PREFIX/MAX_OPEN_MRS/ALLOWED_PROJECTS env vars have no effect —
    warden.toml is the only source of truth for policy tunables (GITLAB_URL/
    GITLAB_MODE/ALLOWED_PROJECTS env vars are likewise ignored)."""
    toml = tmp_path / "warden.toml"
    toml.write_text(_TOML)
    cfg = from_env(
        {
            "BRANCH_PREFIX": "test/",
            "MAX_OPEN_MRS": "1",
            "ALLOWED_PROJECTS": "group/x,group/y",
            "GITLAB_MODE": "off",
            "GITLAB_URL": "https://evil.example",
        },
        toml_path=str(toml),
    )
    assert cfg.branch_prefixes == ("claude/",)  # toml wins, env ignored
    assert cfg.max_open_mrs == 7  # toml wins, env ignored
    assert cfg.allowed_projects == ("group/a", "group/b")  # toml wins, env ignored


def test_missing_toml_uses_builtin_defaults(tmp_path):
    cfg = from_env({}, toml_path=str(tmp_path / "absent.toml"))
    assert cfg.allowed_projects == ()
    assert cfg.max_open_mrs == 5  # built-in default


def test_invalid_toml_type_aborts(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text('max_open_mrs = "lots"\n')
    with pytest.raises(ConfigError, match="integer"):
        from_env({}, strict=False, toml_path=str(toml))


# --- read_tokens/write_tokens files + per-endpoint access mode: access mode
# is derived from token presence, never a declared mode. ---


def test_parse_token_file_splits_host_and_token(tmp_path):
    f = tmp_path / "read_tokens"
    f.write_text("gitlab.com   glpat-abc\nmy-gitlab.de\tglpat-def\n")
    tokens = _parse_token_file({"READ_TOKENS_FILE": str(f)}, "READ_TOKENS")
    assert tokens == {"gitlab.com": "glpat-abc", "my-gitlab.de": "glpat-def"}


def test_parse_token_file_skips_comments_and_blank_lines(tmp_path):
    f = tmp_path / "read_tokens"
    f.write_text("# comment\n\ngitlab.com glpat-abc\n   \n# another\n")
    tokens = _parse_token_file({"READ_TOKENS_FILE": str(f)}, "READ_TOKENS")
    assert tokens == {"gitlab.com": "glpat-abc"}


def test_parse_token_file_normalizes_host():
    tokens = _parse_token_file({"READ_TOKENS": "GitLab.COM:443 glpat-abc\n"}, "READ_TOKENS")
    assert tokens == {"gitlab.com": "glpat-abc"}


def test_parse_token_file_duplicate_host_aborts(tmp_path):
    f = tmp_path / "read_tokens"
    f.write_text("gitlab.com glpat-abc\nGitLab.com glpat-def\n")
    with pytest.raises(ConfigError, match="duplicate host"):
        _parse_token_file({"READ_TOKENS_FILE": str(f)}, "READ_TOKENS")


def test_parse_token_file_absent_yields_empty():
    assert _parse_token_file({}, "READ_TOKENS") == {}


def test_read_tokens_file_missing_raises(tmp_path):
    """The *_FILE indirection raises on an unreadable path."""
    with pytest.raises(ConfigError, match="READ_TOKENS_FILE"):
        from_env({"READ_TOKENS_FILE": str(tmp_path / "nonexistent")}, strict=True)


def test_access_mode_no_tokens_is_closed():
    cfg = Config(git_endpoints=(GitEndpoint(host="gitlab.com", type="gitlab"),))
    assert cfg.access_mode("gitlab.com") == "closed"


def test_access_mode_read_only_is_read_only():
    cfg = Config(
        git_endpoints=(GitEndpoint(host="gitlab.com", type="gitlab"),),
        git_credentials={"gitlab.com": HostCredentials(read_token="r")},
    )
    assert cfg.access_mode("gitlab.com") == "read-only"


def test_access_mode_read_and_write_is_read_write():
    cfg = Config(
        git_endpoints=(GitEndpoint(host="gitlab.com", type="gitlab"),),
        git_credentials={"gitlab.com": HostCredentials(read_token="r", write_token="w")},
    )
    assert cfg.access_mode("gitlab.com") == "read-write"


def test_access_mode_write_without_read_is_closed():
    """Least privilege: a write token never substitutes for a missing read token."""
    cfg = Config(
        git_endpoints=(GitEndpoint(host="gitlab.com", type="gitlab"),),
        git_credentials={"gitlab.com": HostCredentials(write_token="w")},
    )
    assert cfg.access_mode("gitlab.com") == "closed"


def test_access_mode_unconfigured_host_is_closed():
    assert Config().access_mode("gitlab.com") == "closed"


def test_endpoint_credentials_resolved_from_grouped_files(tmp_path):
    read_f = tmp_path / "read_tokens"
    read_f.write_text("gitlab.com r1\nmy-gitlab.de r2\n")
    write_f = tmp_path / "write_tokens"
    write_f.write_text("gitlab.com w1\n")
    toml = tmp_path / "warden.toml"
    toml.write_text(
        '[[git.endpoint]]\nhost = "gitlab.com"\ntype = "gitlab"\n'
        '[[git.endpoint]]\nhost = "my-gitlab.de"\ntype = "gitlab"\n'
    )
    env = {"READ_TOKENS_FILE": str(read_f), "WRITE_TOKENS_FILE": str(write_f)}
    cfg = from_env(env, strict=True, toml_path=str(toml))
    assert cfg.access_mode("gitlab.com") == "read-write"
    assert cfg.access_mode("my-gitlab.de") == "read-only"


def test_missing_read_token_closes_endpoint_with_warning_no_abort(tmp_path, caplog):
    """A configured endpoint with no read token is closed, not a startup abort."""
    toml = tmp_path / "warden.toml"
    toml.write_text('[[git.endpoint]]\nhost = "gitlab.com"\ntype = "gitlab"\n')
    with caplog.at_level("WARNING", logger="warden"):
        cfg = from_env({}, strict=True, toml_path=str(toml))
    assert cfg.access_mode("gitlab.com") == "closed"
    assert "gitlab.com" in caplog.text and "no read token" in caplog.text


def test_write_without_read_closes_endpoint_with_warning_no_abort(tmp_path, caplog):
    write_f = tmp_path / "write_tokens"
    write_f.write_text("gitlab.com w1\n")
    toml = tmp_path / "warden.toml"
    toml.write_text('[[git.endpoint]]\nhost = "gitlab.com"\ntype = "gitlab"\n')
    env = {"WRITE_TOKENS_FILE": str(write_f)}
    with caplog.at_level("WARNING", logger="warden"):
        cfg = from_env(env, strict=True, toml_path=str(toml))
    assert cfg.access_mode("gitlab.com") == "closed"
    assert "gitlab.com" in caplog.text and "read-scoped token" in caplog.text


# --- host_allowed: real default-deny — every routable host is an explicit
# [[git.endpoint]]; an empty list denies every host. ---


def test_host_allowed_empty_endpoint_list_denies_everything():
    """No [[git.endpoint]] configured ⇒ real default-deny, not "feature off"."""
    cfg = Config()
    assert cfg.git_allowed_hosts == frozenset()
    assert not cfg.host_allowed("gitlab.com")
    assert not cfg.host_allowed("anything.example")
    assert not cfg.host_allowed("")


def _open_endpoint(host: str) -> tuple[GitEndpoint, dict[str, HostCredentials]]:
    return GitEndpoint(host=host, type="gitlab"), {
        Config.normalize_host(host): HostCredentials(read_token="r", write_token="w")
    }


def test_host_allowed_allows_a_configured_open_endpoint():
    ep1, creds1 = _open_endpoint("gitlab.com")
    ep2, creds2 = _open_endpoint("my-gitlab.de")
    cfg = Config(git_endpoints=(ep1, ep2), git_credentials={**creds1, **creds2})
    assert cfg.host_allowed("gitlab.com")
    assert cfg.host_allowed("my-gitlab.de")
    assert not cfg.host_allowed("evil.example")
    assert not cfg.host_allowed("")


def test_host_allowed_denies_a_configured_but_closed_endpoint():
    """A host with a [[git.endpoint]] entry but no usable read token is
    denied by the same host gate as an entirely unlisted host — never
    reaches UpstreamRouter.resolve returning None past an "already denied"
    assertion."""
    cfg = Config(git_endpoints=(GitEndpoint(host="gitlab.com", type="gitlab"),))
    assert cfg.access_mode("gitlab.com") == "closed"
    assert not cfg.host_allowed("gitlab.com")


def test_host_allowed_normalizes_case_port_and_trailing_dot():
    ep, creds = _open_endpoint("gitlab.com")
    cfg = Config(git_endpoints=(ep,), git_credentials=creds)
    assert cfg.host_allowed("GitLab.com")
    assert cfg.host_allowed("gitlab.com:443")
    assert cfg.host_allowed("gitlab.com.")


def test_resolve_target_host_unknown_host_returns_none():
    ep, creds = _open_endpoint("gitlab.com")
    cfg = Config(git_endpoints=(ep,), git_credentials=creds)
    assert cfg.resolve_target_host("gitlab.com") == "gitlab.com"
    assert cfg.resolve_target_host("GitLab.com:443") == "gitlab.com"
    assert cfg.resolve_target_host("evil.example") is None


def test_git_section_wrong_shape_aborts_startup(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text("git = 1\n")
    with pytest.raises(ConfigError, match=r"\[git\]"):
        from_env({}, strict=True, toml_path=str(toml))


# --- [git.rules] + [[git.endpoint]] schema: parsing, the rules cascade,
# and per-host lookups. ---


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
    cfg = from_env({}, strict=True, toml_path=str(toml))
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
        from_env({}, strict=True, toml_path=str(toml))


def test_git_endpoint_unknown_type_aborts_startup(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text('[[git.endpoint]]\nhost = "gitlab.com"\ntype = "bitbucket"\n')
    with pytest.raises(ConfigError, match="unknown type"):
        from_env({}, strict=True, toml_path=str(toml))


def test_git_endpoint_github_type_is_reserved_not_implemented(tmp_path):
    """`type = "github"` is a recognised but not-yet-implemented type — a clear,
    distinct error, not silent acceptance of an unguarded forge."""
    toml = tmp_path / "warden.toml"
    toml.write_text('[[git.endpoint]]\nhost = "github.com"\ntype = "github"\n')
    with pytest.raises(ConfigError, match="not implemented"):
        from_env({}, strict=True, toml_path=str(toml))


def test_git_rules_unknown_key_aborts_startup(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text("[git.rules]\nmax_open_mrz = 5\n")  # typo
    with pytest.raises(ConfigError, match="unknown key"):
        from_env({}, strict=True, toml_path=str(toml))


def test_git_endpoint_rules_unknown_key_aborts_startup(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text(
        '[[git.endpoint]]\nhost = "gitlab.com"\ntype = "gitlab"\nrules = { bogus_key = 1 }\n'
    )
    with pytest.raises(ConfigError, match="unknown key"):
        from_env({}, strict=True, toml_path=str(toml))


def test_git_endpoint_missing_host_aborts_startup(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text('[[git.endpoint]]\ntype = "gitlab"\n')
    with pytest.raises(ConfigError, match="host"):
        from_env({}, strict=True, toml_path=str(toml))


# --- [git].actions + per-endpoint actions: parsing, cascade, type-cut ---
# A separate cascade next to rules, same _cascade mechanic as effective_rules.


def test_git_actions_and_endpoint_actions_parsed_from_toml(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text(
        "[git]\n"
        'actions = ["repo.read", "repo.branch.push", "project.mr.comment"]\n'
        "\n"
        "[[git.endpoint]]\n"
        'host = "gitlab.com"\n'
        'type = "gitlab"\n'
        "# no actions key -> inherits [git].actions\n"
        "\n"
        "[[git.endpoint]]\n"
        'host = "my-gitlab.de"\n'
        'type = "gitlab"\n'
        'actions = ["repo.read", "project.mr.comment"]\n'
    )
    cfg = from_env({}, strict=True, toml_path=str(toml))
    assert cfg.git_actions == ("repo.read", "repo.branch.push", "project.mr.comment")

    gitlab_com = cfg.endpoint_for("gitlab.com")
    assert gitlab_com is not None
    assert gitlab_com.actions is None

    my_gitlab = cfg.endpoint_for("my-gitlab.de")
    assert my_gitlab is not None
    assert my_gitlab.actions == ("repo.read", "project.mr.comment")


def test_effective_actions_endpoint_override_replaces_completely():
    cfg = Config(
        git_actions=("repo.read", "repo.branch.push", "project.mr.create", "project.mr.comment"),
        git_endpoints=(
            GitEndpoint(
                host="my-gitlab.de",
                type="gitlab",
                actions=("repo.read", "project.mr.comment"),
            ),
        ),
    )
    assert cfg.effective_actions("my-gitlab.de") == ("repo.read", "project.mr.comment")


def test_effective_actions_missing_endpoint_key_inherits_domain():
    cfg = Config(
        git_actions=("repo.read", "repo.branch.push", "project.mr.create"),
        git_endpoints=(GitEndpoint(host="gitlab.com", type="gitlab"),),
    )
    assert cfg.effective_actions("gitlab.com") == (
        "repo.read",
        "repo.branch.push",
        "project.mr.create",
    )


def test_effective_actions_missing_domain_falls_back_to_builtin_default():
    cfg = Config(git_endpoints=(GitEndpoint(host="gitlab.com", type="gitlab"),))
    assert cfg.effective_actions("gitlab.com") == tuple(sorted(action.id for action in GIT_DEFAULT))


def test_effective_actions_plain_endpoint_inherits_domain_cut_to_repo_ids():
    """A `plain` endpoint that inherits `[git].actions` gets every non-repo.*
    id silently filtered out — no error, unlike an explicit override."""
    cfg = Config(
        git_actions=("repo.read", "repo.branch.push", "project.mr.create", "project.mr.comment"),
        git_endpoints=(GitEndpoint(host="personal-gitserver.it", type="plain"),),
    )
    assert cfg.effective_actions("personal-gitserver.it") == ("repo.read", "repo.branch.push")


def test_effective_actions_plain_endpoint_falls_back_to_builtin_default_cut():
    cfg = Config(git_endpoints=(GitEndpoint(host="personal-gitserver.it", type="plain"),))
    assert cfg.effective_actions("personal-gitserver.it") == (
        "repo.branch.create",
        "repo.branch.push",
        "repo.read",
    )


def test_effective_actions_explicit_empty_list_is_distinguishable_from_absent():
    cfg = Config(
        git_actions=("repo.read", "repo.branch.push"),
        git_endpoints=(GitEndpoint(host="my-gitlab.de", type="gitlab", actions=()),),
    )
    assert cfg.effective_actions("my-gitlab.de") == ()


def test_git_actions_unknown_id_aborts_startup(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text('[git]\nactions = ["repo.read", "repo.reed"]\n')  # typo
    with pytest.raises(ConfigError, match="unknown action id"):
        from_env({}, strict=True, toml_path=str(toml))


def test_git_endpoint_actions_unknown_id_aborts_startup(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text(
        '[[git.endpoint]]\nhost = "gitlab.com"\ntype = "gitlab"\n'
        'actions = ["repo.read", "repo.reed"]\n'  # typo
    )
    with pytest.raises(ConfigError, match="unknown action id"):
        from_env({}, strict=True, toml_path=str(toml))


def test_git_endpoint_explicit_invalid_action_on_plain_type_aborts_startup(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text(
        '[[git.endpoint]]\nhost = "personal-gitserver.it"\ntype = "plain"\n'
        'actions = ["repo.read", "project.mr.create"]\n'
    )
    with pytest.raises(ConfigError, match="not valid for type"):
        from_env({}, strict=True, toml_path=str(toml))


def test_git_actions_non_list_aborts_startup(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text('[git]\nactions = "repo.read"\n')
    with pytest.raises(ConfigError, match="must be a list of strings"):
        from_env({}, strict=True, toml_path=str(toml))


def test_git_endpoint_actions_non_list_aborts_startup(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text(
        '[[git.endpoint]]\nhost = "gitlab.com"\ntype = "gitlab"\nactions = "repo.read"\n'
    )
    with pytest.raises(ConfigError, match="must be a list of strings"):
        from_env({}, strict=True, toml_path=str(toml))
