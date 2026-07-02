"""Unit tests for the pure policy core (W14, §8.1): every rule R0–R6, default-deny."""

from __future__ import annotations

import pytest

from warden.config import Config
from warden.model import ProxyRequest, StateView, TokenKind
from warden.pktline import RefCommand
from warden.policy import check_ref, decide

ZERO = "0" * 40
SHA = "a" * 40


@pytest.fixture
def cfg() -> Config:
    return Config(
        allowed_projects=("group/proj",),
        read_token="r",
        write_token="w",
    )


@pytest.fixture
def multi_prefix_cfg() -> Config:
    # M2: the branch namespace is the *union* of all configured prefixes.
    return Config(
        branch_prefixes=("claude/", "bot/"),
        allowed_projects=("group/proj",),
        read_token="r",
        write_token="w",
    )


def _api(method, path, **fields) -> ProxyRequest:
    project = "group/proj" if "/projects/" in path else ""
    return ProxyRequest(channel="api", project=project, method=method, path=path, fields=fields)


# --- R1 / R6 -------------------------------------------------------------------
def test_r1_get_is_read_passthrough(cfg):
    d = decide(_api("GET", "/projects/group%2Fproj/repository/tree"), StateView(), cfg)
    assert d.allow and d.rule == "R1" and d.token == TokenKind.READ


def test_r1_get_without_project_allowed(cfg):
    d = decide(_api("GET", "/user"), StateView(), cfg)
    assert d.allow and d.token == TokenKind.READ


# --- B1: projectless read-endpoint table ("content, not visibility") -----------
@pytest.mark.parametrize(
    "path",
    [
        "/projects",
        "/users",
        "/users/7",
        "/user",
        "/user/keys",
        "/version",
        "/metadata",
        "/groups",
        "/groups/1",
        "/groups/1/projects",  # AGENT.md discovery flow — must keep working
        "/groups/1/subgroups",
        "/groups/1/descendant_groups",
        "/merge_requests",
        "/issues",
        "/events",
        "/broadcast_messages",
    ],
)
def test_b1_projectless_metadata_endpoints_allowed(cfg, path):
    d = decide(_api("GET", path), StateView(), cfg)
    assert d.allow and d.rule == "R1" and d.token == TokenKind.READ


@pytest.mark.parametrize("scope", ["blobs", "commits", "wiki_blobs", "notes"])
def test_b1_global_search_content_scope_denied(cfg, scope):
    d = decide(_api("GET", "/search", scope=scope), StateView(), cfg)
    assert not d.allow and d.rule == "R6"


def test_b1_global_search_without_scope_denied_fail_closed(cfg):
    d = decide(_api("GET", "/search"), StateView(), cfg)
    assert not d.allow and d.rule == "R6"


def test_b1_global_search_unknown_scope_denied_fail_closed(cfg):
    d = decide(_api("GET", "/search", scope="commit_titles_or_whatever"), StateView(), cfg)
    assert not d.allow and d.rule == "R6"


@pytest.mark.parametrize("scope", ["projects", "issues", "merge_requests", "milestones", "users"])
def test_b1_global_search_metadata_scope_allowed(cfg, scope):
    d = decide(_api("GET", "/search", scope=scope), StateView(), cfg)
    assert d.allow and d.rule == "R1" and d.token == TokenKind.READ


def test_b1_group_search_content_scope_denied(cfg):
    d = decide(_api("GET", "/groups/1/search", scope="blobs"), StateView(), cfg)
    assert not d.allow and d.rule == "R6"


def test_b1_snippets_denied(cfg):
    d = decide(_api("GET", "/snippets"), StateView(), cfg)
    assert not d.allow and d.rule == "R6"


def test_b1_snippet_subpath_denied(cfg):
    d = decide(_api("GET", "/snippets/1/raw"), StateView(), cfg)
    assert not d.allow and d.rule == "R6"


def test_b1_unknown_projectless_endpoint_default_denied(cfg):
    d = decide(_api("GET", "/admin/ci/variables"), StateView(), cfg)
    assert not d.allow and d.rule == "R6"
    assert "not in allowlist" in d.reason


def test_r6_project_not_in_allowlist_denied(cfg):
    req = ProxyRequest(channel="api", project="other/secret", method="GET", path="/projects/other%2Fsecret")
    d = decide(req, StateView(), cfg)
    assert not d.allow and d.rule == "R6"


# --- R3 create / ownership -----------------------------------------------------
def test_r3_create_mr_with_prefix_allowed(cfg):
    d = decide(
        _api("POST", "/projects/group%2Fproj/merge_requests", source_branch="claude/x"),
        StateView(),
        cfg,
    )
    assert d.allow and d.rule == "R3" and d.token == TokenKind.WRITE


def test_r2_create_mr_wrong_prefix_denied(cfg):
    d = decide(
        _api("POST", "/projects/group%2Fproj/merge_requests", source_branch="feature/x"),
        StateView(),
        cfg,
    )
    assert not d.allow and d.rule == "R2"


def test_r3_note_requires_ownership(cfg):
    req = _api("POST", "/projects/group%2Fproj/merge_requests/7/notes")
    req.mr_owner_ok = True
    assert decide(req, StateView(), cfg).allow
    req.mr_owner_ok = False
    d = decide(req, StateView(), cfg)
    assert not d.allow and d.rule == "R3"
    req.mr_owner_ok = None  # unverifiable → default-deny
    d = decide(req, StateView(), cfg)
    assert not d.allow and d.rule == "R3"


def test_r3_pipeline_ref_prefix(cfg):
    ok = _api("POST", "/projects/group%2Fproj/pipeline", ref="claude/x")
    assert decide(ok, StateView(), cfg).allow
    bad = _api("POST", "/projects/group%2Fproj/pipeline", ref="main")
    assert not decide(bad, StateView(), cfg).allow


# --- M2: branch namespace is a list of prefixes (Maintainer-Entscheid) --------
def test_r3_create_mr_with_second_prefix_allowed(multi_prefix_cfg):
    """A source_branch under the *second* configured prefix (``bot/``) is allowed."""
    d = decide(
        _api("POST", "/projects/group%2Fproj/merge_requests", source_branch="bot/x"),
        StateView(),
        multi_prefix_cfg,
    )
    assert d.allow and d.rule == "R3" and d.token == TokenKind.WRITE


def test_r2_create_mr_outside_all_prefixes_denied(multi_prefix_cfg):
    d = decide(
        _api("POST", "/projects/group%2Fproj/merge_requests", source_branch="feature/x"),
        StateView(),
        multi_prefix_cfg,
    )
    assert not d.allow and d.rule == "R2"


# --- R4 merge block ------------------------------------------------------------
def test_r4_merge_endpoint_always_denied(cfg):
    d = decide(_api("PUT", "/projects/group%2Fproj/merge_requests/7/merge"), StateView(), cfg)
    assert not d.allow and d.rule == "R4"


def test_r4_state_event_merge_alias_denied(cfg):
    req = _api("PUT", "/projects/group%2Fproj/merge_requests/7", state_event="merge")
    req.mr_owner_ok = True
    d = decide(req, StateView(), cfg)
    assert not d.allow and d.rule == "R4"


def test_default_deny_unknown_write_endpoint(cfg):
    d = decide(_api("DELETE", "/projects/group%2Fproj/repository/branches/claude%2Fx"), StateView(), cfg)
    assert not d.allow and d.rule == "R3"


# --- R5 quotas -----------------------------------------------------------------
def test_r5_rate_limit_blocks_writes(cfg):
    state = StateView(writes_last_hour=cfg.max_writes_per_hour)
    d = decide(
        _api("POST", "/projects/group%2Fproj/merge_requests", source_branch="claude/x"),
        state,
        cfg,
    )
    assert not d.allow and d.rule == "R5"


def test_r5_max_open_mrs_blocks_mr_creation(cfg):
    state = StateView(open_mrs=cfg.max_open_mrs)
    d = decide(
        _api("POST", "/projects/group%2Fproj/merge_requests", source_branch="claude/x"),
        state,
        cfg,
    )
    assert not d.allow and d.rule == "R5"


def test_locked_state_denies_all_writes(cfg):
    state = StateView(locked=True)
    d = decide(
        _api("POST", "/projects/group%2Fproj/merge_requests", source_branch="claude/x"),
        state,
        cfg,
    )
    assert not d.allow and d.rule == "R5"


# --- git channel (R2/R5) -------------------------------------------------------
def _git(*cmds) -> ProxyRequest:
    return ProxyRequest(
        channel="git",
        project="group/proj.git",
        ref_commands=[RefCommand(*c) for c in cmds],
    )


def test_git_push_prefixed_branch_allowed(cfg):
    d = decide(_git((ZERO, SHA, "refs/heads/claude/feature")), StateView(), cfg)
    assert d.allow and d.token == TokenKind.WRITE


def test_git_push_wrong_prefix_denied(cfg):
    d = decide(_git((ZERO, SHA, "refs/heads/main")), StateView(), cfg)
    assert not d.allow and d.rule == "R2"


def test_git_push_second_prefix_allowed(multi_prefix_cfg):
    """Push to a branch under the *second* configured prefix (``bot/``) is allowed."""
    d = decide(_git((ZERO, SHA, "refs/heads/bot/feature")), StateView(), multi_prefix_cfg)
    assert d.allow and d.token == TokenKind.WRITE


def test_git_push_outside_all_prefixes_denied(multi_prefix_cfg):
    d = decide(_git((ZERO, SHA, "refs/heads/other/feature")), StateView(), multi_prefix_cfg)
    assert not d.allow and d.rule == "R2"


def test_git_branch_delete_denied(cfg):
    d = decide(_git((SHA, ZERO, "refs/heads/claude/feature")), StateView(), cfg)
    assert not d.allow and d.rule == "R2"


def test_git_atomic_reject_on_one_bad_ref(cfg):
    d = decide(
        _git(
            (ZERO, SHA, "refs/heads/claude/ok"),
            (ZERO, SHA, "refs/heads/evil"),
        ),
        StateView(),
        cfg,
    )
    assert not d.allow and d.rule == "R2"


def test_git_max_branches_blocks_create(cfg):
    state = StateView(open_branches=cfg.max_open_branches)
    d = decide(_git((ZERO, SHA, "refs/heads/claude/new")), state, cfg)
    assert not d.allow and d.rule == "R5"


def test_git_locked_state_denies_push(cfg):
    state = StateView(locked=True)
    d = decide(_git((ZERO, SHA, "refs/heads/claude/feature")), state, cfg)
    assert not d.allow and d.rule == "R5"


def test_git_rate_limit_blocks_push(cfg):
    state = StateView(writes_last_hour=cfg.max_writes_per_hour)
    d = decide(_git((ZERO, SHA, "refs/heads/claude/feature")), state, cfg)
    assert not d.allow and d.rule == "R5"


def test_git_multiref_quota_accounts_within_batch(cfg):
    # max-1 open branches + two creates in one push must reject the batch (not
    # let both pass against the same stale snapshot).
    state = StateView(open_branches=cfg.max_open_branches - 1)
    d = decide(
        _git(
            (ZERO, SHA, "refs/heads/claude/a"),
            (ZERO, SHA, "refs/heads/claude/b"),
        ),
        state,
        cfg,
    )
    assert not d.allow and d.rule == "R5"


def test_git_tag_push_rejected_with_tag_message(cfg):
    d = check_ref(RefCommand(ZERO, SHA, "refs/tags/claude/v1"), StateView(), cfg)
    assert not d.allow and d.rule == "R2" and "tag" in d.reason


def test_git_project_not_allowlisted_denied(cfg):
    req = ProxyRequest(
        channel="git",
        project="other/x.git",
        ref_commands=[RefCommand(ZERO, SHA, "refs/heads/claude/x")],
    )
    d = decide(req, StateView(), cfg)
    assert not d.allow and d.rule == "R6"


def test_git_empty_push_denied(cfg):
    # A push that carries no ref commands has nothing to authorise → default-deny.
    d = decide(_git(), StateView(), cfg)
    assert not d.allow and d.rule == "R2"


# --- remaining allow / default-deny edges --------------------------------------
def test_mr_update_without_merge_intent_allowed(cfg):
    # The non-merge edit path: owned MR, no state_event=merge → allowed (R3).
    req = _api("PUT", "/projects/group%2Fproj/merge_requests/7", title="new title")
    req.mr_owner_ok = True
    d = decide(req, StateView(), cfg)
    assert d.allow and d.rule == "R3" and d.token == TokenKind.WRITE


def test_unknown_channel_default_denied(cfg):
    d = decide(ProxyRequest(channel="bogus", project=""), StateView(), cfg)
    assert not d.allow and d.rule == "R6"


# --- R0: GITLAB_MODE gates -------------------------------------------------------

def test_off_denies_reads_and_writes():
    """GITLAB_MODE=off: both reads and writes are denied (R0) — no GitLab traffic at all."""
    cfg_off = Config(
        allowed_projects=("group/proj",),
        read_token="r",
        write_token="w",
        gitlab_mode="off",
    )
    # API read
    d = decide(_api("GET", "/projects/group%2Fproj/repository/tree"), StateView(), cfg_off)
    assert not d.allow and d.rule == "R0" and "off" in d.reason

    # API write
    d = decide(
        _api("POST", "/projects/group%2Fproj/merge_requests", source_branch="claude/x"),
        StateView(),
        cfg_off,
    )
    assert not d.allow and d.rule == "R0"

    # git push
    d = decide(
        ProxyRequest(
            channel="git",
            project="group/proj",
            ref_commands=[RefCommand(ZERO, SHA, "refs/heads/claude/x")],
        ),
        StateView(),
        cfg_off,
    )
    assert not d.allow and d.rule == "R0"


def test_read_only_denies_writes_allows_reads():
    """GITLAB_MODE=read-only: reads pass (R1), writes are denied (R0)."""
    cfg_ro = Config(
        allowed_projects=("group/proj",),
        read_token="r",
        write_token="",
        gitlab_mode="read-only",
    )
    # API read: allowed
    d = decide(_api("GET", "/projects/group%2Fproj/repository/tree"), StateView(), cfg_ro)
    assert d.allow and d.rule == "R1"

    # API write: denied R0
    d = decide(
        _api("POST", "/projects/group%2Fproj/merge_requests", source_branch="claude/x"),
        StateView(),
        cfg_ro,
    )
    assert not d.allow and d.rule == "R0" and "read-only" in d.reason

    # git push: denied R0
    d = decide(
        ProxyRequest(
            channel="git",
            project="group/proj",
            ref_commands=[RefCommand(ZERO, SHA, "refs/heads/claude/x")],
        ),
        StateView(),
        cfg_ro,
    )
    assert not d.allow and d.rule == "R0" and "read-only" in d.reason
