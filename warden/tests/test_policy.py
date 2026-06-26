"""Unit tests for the pure policy core (W14, §8.1): every rule R1–R6, default-deny."""

from __future__ import annotations

import pytest

from warden.config import Config
from warden.pktline import RefCommand
from warden.policy import ProxyRequest, StateView, TokenKind, decide

ZERO = "0" * 40
SHA = "a" * 40


@pytest.fixture
def cfg() -> Config:
    return Config(
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


def test_r6_project_not_in_allowlist_denied(cfg):
    req = ProxyRequest(channel="api", project="other/secret", method="GET", path="/projects/other%2Fsecret")
    req.project = "other/secret"
    d = decide(req, StateView(), cfg)
    assert not d.allow and d.rule == "R6"


# --- R3 create / ownership -----------------------------------------------------
def test_r3_create_mr_with_prefix_allowed(cfg):
    d = decide(
        _api("POST", "/projects/group%2Fproj/merge_requests", source_branch="claude/x"),
        StateView(),
        cfg,
    )
    assert d.allow and d.token == TokenKind.WRITE


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
    assert not decide(req, StateView(), cfg).allow
    req.mr_owner_ok = None  # unverifiable → default-deny
    assert not decide(req, StateView(), cfg).allow


def test_r3_pipeline_ref_prefix(cfg):
    ok = _api("POST", "/projects/group%2Fproj/pipeline", ref="claude/x")
    assert decide(ok, StateView(), cfg).allow
    bad = _api("POST", "/projects/group%2Fproj/pipeline", ref="main")
    assert not decide(bad, StateView(), cfg).allow


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


def test_git_project_not_allowlisted_denied(cfg):
    req = ProxyRequest(
        channel="git",
        project="other/x.git",
        ref_commands=[RefCommand(ZERO, SHA, "refs/heads/claude/x")],
    )
    d = decide(req, StateView(), cfg)
    assert not d.allow and d.rule == "R6"
