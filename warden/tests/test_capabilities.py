"""Golden tests for the capability-invariant layer (§03.4, B2,
§06-migration.md Schritt 3/4): known requests → expected capability sets, plus
the cross-channel invariant that a FORBIDDEN hit denies with R4 regardless of
channel or endpoint-specific checks.

Schritt 4 moved the write-endpoint table into the catalog package and took the
merge endpoint out of it entirely (it is now a built-in deny invariant, not a
catalog row — ``catalog.builtin``); Schritt 5 split the vocabulary/FORBIDDEN
set (kernel, ``warden.core.capabilities``) from the per-guard intent→capability
mappings (``guards.git.policy.git_ref_capabilities``,
``guards.gitlab_api.catalog.entries.api_capabilities``) — this file's golden
tables were updated accordingly; see ``tests/catalog/`` for the catalog/
activation-specific tests Schritt 4 added.
"""

from __future__ import annotations

import pytest

from warden.core.capabilities import FORBIDDEN, Capability, forbidden_check
from warden.core.config import Config
from warden.core.model import StateView
from warden.guards.git import policy as git_policy
from warden.guards.git.intent import GitIntent
from warden.guards.git.pktline import RefCommand
from warden.guards.git.policy import git_ref_capabilities
from warden.guards.gitlab_api.catalog import (
    CATALOG,
    DEFAULT_ENABLED,
    EndpointKind,
    Recognizer,
    ScopeKind,
    api_capabilities,
)
from warden.guards.gitlab_api.catalog.builtin import is_builtin_merge_endpoint
from warden.guards.gitlab_api.intent import ApiIntent
from warden.guards.gitlab_api.policy import full_decide as api_decide

ZERO = "0" * 40
SHA = "a" * 40
SHA2 = "b" * 40


@pytest.fixture
def cfg() -> Config:
    return Config(allowed_projects=("group/proj",), read_token="r", write_token="w")


def _api(method: str, path: str, **fields: object) -> ApiIntent:
    project = "group/proj" if "/projects/" in path else ""
    return ApiIntent(_project=project, _method=method, path=path, fields=dict(fields))


# --- the vocabulary itself ------------------------------------------------


def test_capability_vocabulary_is_exactly_the_documented_set():
    assert {c.value for c in Capability} == {
        "creates_ref",
        "deletes_ref",
        "creates_tag",
        "merges",
        "escalates_privilege",
        "writes_outside_namespace",
        "destroys_data",
    }


def test_forbidden_is_a_frozenset_with_exactly_the_documented_members():
    # Guards against accidental widening/narrowing of the compiled-in
    # invariant (§06.2: never configurable, so this must stay a hard-coded
    # constant this test can pin down).
    assert isinstance(FORBIDDEN, frozenset)
    assert FORBIDDEN == {
        Capability.DELETES_REF,
        Capability.CREATES_TAG,
        Capability.MERGES,
        Capability.ESCALATES_PRIVILEGE,
        Capability.DESTROYS_DATA,
    }
    # creates_ref and writes_outside_namespace are in the vocabulary but not
    # forbidden (see FORBIDDEN's docstring) — pin that down too.
    assert Capability.CREATES_REF not in FORBIDDEN
    assert Capability.WRITES_OUTSIDE_NAMESPACE not in FORBIDDEN


# --- forbidden_check, the layer in isolation -------------------------------


@pytest.mark.parametrize(
    "cap",
    [
        Capability.DELETES_REF,
        Capability.CREATES_TAG,
        Capability.MERGES,
        Capability.ESCALATES_PRIVILEGE,
        Capability.DESTROYS_DATA,
    ],
)
def test_forbidden_check_denies_each_forbidden_capability_with_r4(cap):
    d = forbidden_check(frozenset({cap}))
    assert d is not None
    assert not d.allow and d.rule == "R4"
    assert cap.value in d.reason


@pytest.mark.parametrize(
    "caps",
    [
        frozenset(),
        frozenset({Capability.CREATES_REF}),
        frozenset({Capability.WRITES_OUTSIDE_NAMESPACE}),
        frozenset({Capability.CREATES_REF, Capability.WRITES_OUTSIDE_NAMESPACE}),
    ],
)
def test_forbidden_check_passes_non_forbidden_capabilities(caps):
    assert forbidden_check(caps) is None


def test_forbidden_check_names_every_violated_capability():
    d = forbidden_check(frozenset({Capability.MERGES, Capability.DELETES_REF}))
    assert d is not None
    assert "merges" in d.reason and "deletes_ref" in d.reason


# --- git: intent -> capability mapping (trivial and exact, §03.4) ----------


@pytest.mark.parametrize(
    "old,new,ref,expected",
    [
        # Non-deleting tag push: creates_tag.
        (ZERO, SHA, "refs/tags/claude/v1", {Capability.CREATES_TAG}),
        (SHA, SHA2, "refs/tags/claude/v1", {Capability.CREATES_TAG}),
        # Any delete: deletes_ref, regardless of ref type.
        (SHA, ZERO, "refs/tags/claude/v1", {Capability.DELETES_REF}),
        (SHA, ZERO, "refs/heads/claude/feature", {Capability.DELETES_REF}),
        # Branch create inside the namespace: creates_ref only.
        (ZERO, SHA, "refs/heads/claude/feature", {Capability.CREATES_REF}),
        # Branch create outside the namespace: both creates_ref and
        # writes_outside_namespace (neither forbidden by itself).
        (
            ZERO,
            SHA,
            "refs/heads/main",
            {Capability.CREATES_REF, Capability.WRITES_OUTSIDE_NAMESPACE},
        ),
        # Fast-forward update (neither create nor delete) inside namespace:
        # no capability at all.
        (SHA, SHA2, "refs/heads/claude/feature", set()),
        # Fast-forward update outside the namespace: writes_outside_namespace only.
        (SHA, SHA2, "refs/heads/main", {Capability.WRITES_OUTSIDE_NAMESPACE}),
    ],
)
def test_git_ref_capabilities_golden_table(cfg, old, new, ref, expected):
    caps = git_ref_capabilities(RefCommand(old, new, ref), cfg)
    assert caps == frozenset(expected)


# --- REST: every CATALOG row, plus the field-dependent merge alias --------


def _endpoint(template: str, method: str) -> Recognizer:
    for ep in CATALOG:
        if ep.template == template and ep.method == method:
            return ep
    raise AssertionError(f"no such catalog entry: {method} {template}")


@pytest.mark.parametrize(
    "method,template,fields,expected",
    [
        (
            "POST",
            "/projects/{id}/merge_requests",
            {"source_branch": "claude/x"},
            set(),
        ),
        ("POST", "/projects/{id}/merge_requests/{iid}/notes", {}, set()),
        ("POST", "/projects/{id}/merge_requests/{iid}/discussions", {}, set()),
        (
            "POST",
            "/projects/{id}/merge_requests/{iid}/discussions/{discussion_id}/notes",
            {},
            set(),
        ),
        # MR update: no state_event → empty (editing title/description).
        ("PUT", "/projects/{id}/merge_requests/{iid}", {"title": "x"}, set()),
        # MR update: state_event=merge alias → merges, field-dependent. This is
        # the FORBIDDEN-layer proof that the merge alias is closed even though
        # the raw merge endpoint itself is no longer a catalog row at all.
        (
            "PUT",
            "/projects/{id}/merge_requests/{iid}",
            {"state_event": "merge"},
            {Capability.MERGES},
        ),
        # A non-merge state_event (e.g. "close") stays empty.
        ("PUT", "/projects/{id}/merge_requests/{iid}", {"state_event": "close"}, set()),
        ("POST", "/projects/{id}/pipeline", {"ref": "claude/x"}, set()),
        # Extra, non-default catalog entries (§04.2) — honestly catalogued
        # capabilities, golden-tested like every other row.
        (
            "POST",
            "/projects/{id}/repository/branches",
            {"branch": "claude/x"},
            {Capability.CREATES_REF},
        ),
        ("POST", "/projects/{id}/issues", {"title": "x"}, set()),
    ],
)
def test_api_capabilities_golden_table(method, template, fields, expected):
    ep = _endpoint(template, method)
    assert api_capabilities(ep, fields) == frozenset(expected)


def test_every_catalog_row_is_covered_by_the_golden_table():
    # Guards against a new CATALOG row being added without a matching
    # golden-table entry above (silent capability coverage gap, §03.4's
    # "honest cost"). The merge endpoint is deliberately absent — it is a
    # built-in deny invariant (builtin.py), not a catalog row (§04.2).
    covered = {
        ("POST", "/projects/{id}/merge_requests"),
        ("POST", "/projects/{id}/merge_requests/{iid}/notes"),
        ("POST", "/projects/{id}/merge_requests/{iid}/discussions"),
        ("POST", "/projects/{id}/merge_requests/{iid}/discussions/{discussion_id}/notes"),
        ("PUT", "/projects/{id}/merge_requests/{iid}"),
        ("POST", "/projects/{id}/pipeline"),
        ("POST", "/projects/{id}/repository/branches"),
        ("POST", "/projects/{id}/issues"),
    }
    actual = {(ep.method, ep.template) for ep in CATALOG}
    assert actual == covered


def test_default_enabled_is_exactly_the_pre_schritt4_active_set():
    # §04.2/04.3 behaviour preservation: the shipped default set must be
    # exactly what was unconditionally active before the catalog existed.
    assert DEFAULT_ENABLED == {
        "mr.create",
        "mr.note",
        "mr.discussion",
        "mr.discussion_reply",
        "mr.update",
        "pipeline.trigger",
    }
    # And the two extra entries are honestly catalogued but NOT default.
    assert "branch.create" not in DEFAULT_ENABLED
    assert "issue.create" not in DEFAULT_ENABLED


def test_every_catalog_entry_has_an_id():
    # §04.2: the id is the stable name activation config and CLI match
    # against — every row in CATALOG must carry one.
    for ep in CATALOG:
        assert ep.id, f"catalog entry with empty id: {ep.method} {ep.template}"


def test_no_catalog_entry_declares_a_forbidden_capability():
    # §04.2 YAGNI: activating an entry whose capabilities intersect FORBIDDEN
    # is refused at startup (activation.py) — but nothing stops a catalog PR
    # from *authoring* such a row today (no taming mechanism exists yet). Pin
    # down that none of the entries actually shipped do this, so that
    # invariant is never silently relied upon by a real default-enabled row.
    for ep in CATALOG:
        assert not (ep.capabilities & FORBIDDEN), f"{ep.id!r} declares a FORBIDDEN capability"


# --- the built-in merge invariant (§04.2) — not a catalog row -------------


def test_merge_endpoint_is_not_a_catalog_row():
    assert not any(ep.template.endswith("/merge") for ep in CATALOG)


@pytest.mark.parametrize(
    "method,path,expected",
    [
        ("PUT", "/projects/group%2Fproj/merge_requests/7/merge", True),
        ("put", "/projects/group%2Fproj/merge_requests/7/merge", True),  # case-insensitive method
        ("POST", "/projects/group%2Fproj/merge_requests/7/merge", False),  # wrong method
        ("PUT", "/projects/group%2Fproj/merge_requests/7", False),  # not the merge sub-path
    ],
)
def test_is_builtin_merge_endpoint(method, path, expected):
    assert is_builtin_merge_endpoint(method, path) is expected


# --- end-to-end via decide(): the invariant holds on both channels --------


def test_e2e_git_tag_push_denied_r4(cfg):
    req = GitIntent(
        _project="group/proj",
        operation="receive-pack",
        _method="push",
        _writes=True,
        ref_commands=[RefCommand(ZERO, SHA, "refs/tags/claude/v1")],
    )
    d = git_policy.full_decide(req, StateView(), cfg)
    assert not d.allow and d.rule == "R4"


def test_e2e_git_branch_delete_denied_r4(cfg):
    req = GitIntent(
        _project="group/proj",
        operation="receive-pack",
        _method="push",
        _writes=True,
        ref_commands=[RefCommand(SHA, ZERO, "refs/heads/claude/feature")],
    )
    d = git_policy.full_decide(req, StateView(), cfg)
    assert not d.allow and d.rule == "R4"


def test_e2e_api_merge_endpoint_denied_r4(cfg):
    d = api_decide(_api("PUT", "/projects/group%2Fproj/merge_requests/7/merge"), StateView(), cfg)
    assert not d.allow and d.rule == "R4"


def test_e2e_api_state_event_merge_alias_denied_r4(cfg):
    req = _api("PUT", "/projects/group%2Fproj/merge_requests/7", state_event="merge")
    req.mr_source_ok = True
    d = api_decide(req, StateView(), cfg)
    assert not d.allow and d.rule == "R4"


def test_e2e_capability_layer_denies_even_without_endpoint_checks(cfg):
    """Proves the capability layer is structural, not just a lucky
    consequence of an endpoint's own checks (§03.4): a hypothetical catalog
    row — one that is *not* shaped like the built-in merge endpoint, so this
    genuinely exercises the capability layer and not ``is_builtin_merge_endpoint``
    — that declares the merge capability but has *no* checks at all is still
    denied, because the capability gate runs before the guard's own
    ``decide``/``ep.checks`` (kernel sequence, §03.2).
    """
    hypothetical_row = Recognizer(
        id="hypothetical.merge_via_release",
        method="POST",
        template="/projects/{id}/releases",
        scope_kind=ScopeKind.QUOTA_BY_KIND,  # no scope check whatsoever — the old-style defense is gone
        rule="R4",
        kind=EndpointKind.MERGE,
        capabilities=frozenset({Capability.MERGES}),
    )
    req = _api("POST", "/projects/group%2Fproj/releases")
    req.endpoint = hypothetical_row
    d = api_decide(req, StateView(), cfg)
    assert not d.allow and d.rule == "R4"
    assert "forbidden capability" in d.reason


def test_e2e_capability_layer_denies_git_even_if_check_ref_logic_is_bypassed(cfg):
    """Mirrors the API-side proof above for git: the capability gate runs for
    every ref-command *before* the guard's ``check_ref`` loop (kernel
    sequence, §03.2), so a tag push is denied at that first pass —
    independent of whatever ``check_ref`` itself would separately decide.
    """
    req = GitIntent(
        _project="group/proj",
        operation="receive-pack",
        _method="push",
        _writes=True,
        ref_commands=[RefCommand(ZERO, SHA, "refs/tags/claude/v1")],
    )
    d = git_policy.full_decide(req, StateView(), cfg)
    assert not d.allow and d.rule == "R4"
    assert "forbidden capability" in d.reason  # came from the capability layer, not check_ref
