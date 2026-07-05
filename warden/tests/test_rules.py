"""rules.py (B3, F11, §06-migration.md Schritt 2): the rule registry itself,
plus the cross-module invariant that every ``Decision.rule`` produced by the
policy actually comes from the registry.
"""

from __future__ import annotations

import pytest

from warden.core.config import Config
from warden.core.model import StateView
from warden.core.rules import (
    GITLAB_NAMESPACE,
    KERNEL_NAMESPACE,
    RULES,
    MetaRule,
    RuleDef,
    qualify,
    rule,
)
from warden.guards.git.gitlab.intent import ApiIntent
from warden.guards.git.gitlab.policy import full_decide as decide
from warden.guards.git.transport.pktline import RefCommand
from warden.guards.git.transport.policy import ref_action_gate

ZERO = "0" * 40
SHA = "a" * 40


# --- the registry itself ---------------------------------------------------


def test_registry_has_exactly_r0_through_r6():
    assert set(RULES) == {"R0", "R1", "R2", "R3", "R4", "R5", "R6"}


@pytest.mark.parametrize("rule_id", sorted(RULES))
def test_every_rule_def_is_well_formed(rule_id):
    d = RULES[rule_id]
    assert isinstance(d, RuleDef)
    assert d.id == rule_id
    assert isinstance(d.meta, MetaRule)
    assert d.summary  # non-empty, human-readable


def test_rule_looks_up_a_known_id():
    assert rule("R4").meta == MetaRule.M4


def test_rule_raises_on_unknown_id():
    with pytest.raises(KeyError):
        rule("R99")


def test_r4_is_the_irreversible_verbs_meta_rule():
    # B3: this is precisely the partition that was missing — merge, tag push,
    # and branch delete are all the *same* meta-rule (M4, "never").
    assert rule("R4").meta == MetaRule.M4


def test_r2_is_the_namespace_meta_rule_not_never():
    # B3: R2 is the branch-namespace check (M2) — it must NOT also carry the
    # "never" semantics that used to leak in via tag-push/branch-delete.
    assert rule("R2").meta == MetaRule.M2


# --- kernel namespace (prepared, not yet emitted) ---------------------------


def test_qualify_builds_a_namespaced_id():
    assert qualify("R4") == f"{GITLAB_NAMESPACE}.R4"
    assert qualify("R5", namespace=KERNEL_NAMESPACE) == "core.R5"


def test_qualify_rejects_unknown_rule_id():
    with pytest.raises(KeyError):
        qualify("R99")


def test_qualify_rejects_empty_namespace():
    with pytest.raises(ValueError):
        qualify("R4", namespace="")


# --- cross-module: every produced Decision.rule is registered --------------


@pytest.fixture
def cfg() -> Config:
    return Config(allowed_projects=("group/proj",))


def _api(method, path, **fields) -> ApiIntent:
    project = "group/proj" if "/projects/" in path else ""
    return ApiIntent(_project=project, _method=method, path=path, fields=fields)


# One representative request per rule id the policy can emit — a smoke test
# that every id `decide()`/`ref_action_gate()` actually produce traces back to
# the registry (the invariant the whole module exists to guarantee).
@pytest.mark.parametrize(
    "make_decision",
    [
        lambda cfg: decide(_api("GET", "/projects/group%2Fproj/repository/tree"), StateView(), cfg),
        lambda cfg: decide(_api("GET", "/admin/ci/variables"), StateView(), cfg),
        lambda cfg: decide(
            _api("POST", "/projects/group%2Fproj/merge_requests", source_branch="feature/x"),
            StateView(),
            cfg,
        ),
        lambda cfg: decide(
            _api("POST", "/projects/group%2Fproj/merge_requests", source_branch="claude/x"),
            StateView(),
            cfg,
        ),
        lambda cfg: decide(
            _api("PUT", "/projects/group%2Fproj/merge_requests/7/merge"), StateView(), cfg
        ),
        lambda cfg: decide(
            _api("POST", "/projects/group%2Fproj/merge_requests", source_branch="claude/x"),
            StateView(writes_last_hour=cfg.max_writes_per_hour),
            cfg,
        ),
        lambda cfg: decide(
            ApiIntent(_project="other/x", _method="GET", path="/projects/other%2Fx"),
            StateView(),
            cfg,
        ),
    ],
)
def test_decide_rule_is_always_registered(cfg, make_decision):
    d = make_decision(cfg)
    assert rule(d.rule) is not None  # raises KeyError if not registered


def test_git_tag_push_and_branch_delete_are_registered_as_r4(cfg):
    tag = ref_action_gate(RefCommand(ZERO, SHA, "refs/tags/claude/v1"), "h", cfg)
    delete = ref_action_gate(RefCommand(SHA, ZERO, "refs/heads/claude/feature"), "h", cfg)
    assert tag is not None and tag.rule == "R4" and rule(tag.rule).meta == MetaRule.M4
    assert delete is not None and delete.rule == "R4" and rule(delete.rule).meta == MetaRule.M4
