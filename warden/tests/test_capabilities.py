"""Golden tests for the capability-invariant layer (§03.4, B2,
§06-migration.md Schritt 3): known requests → expected capability sets, plus
the cross-channel invariant that a FORBIDDEN hit denies with R4 regardless of
channel or endpoint-specific checks.
"""

from __future__ import annotations

import pytest

from warden.api_endpoints import (
    WRITE_ENDPOINTS,
    EndpointKind,
    WriteEndpoint,
    api_capabilities,
)
from warden.capabilities import FORBIDDEN, Capability, forbidden_check, git_ref_capabilities
from warden.config import Config
from warden.model import ProxyRequest, StateView
from warden.pktline import RefCommand
from warden.policy import decide

ZERO = "0" * 40
SHA = "a" * 40
SHA2 = "b" * 40


@pytest.fixture
def cfg() -> Config:
    return Config(allowed_projects=("group/proj",), read_token="r", write_token="w")


def _api(method: str, path: str, **fields: object) -> ProxyRequest:
    project = "group/proj" if "/projects/" in path else ""
    return ProxyRequest(channel="api", project=project, method=method, path=path, fields=fields)


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
        (ZERO, SHA, "refs/heads/main", {Capability.CREATES_REF, Capability.WRITES_OUTSIDE_NAMESPACE}),
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


# --- REST: every WRITE_ENDPOINTS row, plus the field-dependent merge alias -


def _endpoint(template: str, method: str) -> WriteEndpoint:
    for ep in WRITE_ENDPOINTS:
        if ep.template == template and ep.method == method:
            return ep
    raise AssertionError(f"no such endpoint row: {method} {template}")


@pytest.mark.parametrize(
    "method,template,fields,expected",
    [
        ("PUT", "/projects/{id}/merge_requests/{iid}/merge", {}, {Capability.MERGES}),
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
        # MR update: state_event=merge alias → merges, field-dependent.
        (
            "PUT",
            "/projects/{id}/merge_requests/{iid}",
            {"state_event": "merge"},
            {Capability.MERGES},
        ),
        # A non-merge state_event (e.g. "close") stays empty.
        ("PUT", "/projects/{id}/merge_requests/{iid}", {"state_event": "close"}, set()),
        ("POST", "/projects/{id}/pipeline", {"ref": "claude/x"}, set()),
    ],
)
def test_api_capabilities_golden_table(method, template, fields, expected):
    ep = _endpoint(template, method)
    assert api_capabilities(ep, fields) == frozenset(expected)


def test_every_write_endpoint_row_is_covered_by_the_golden_table():
    # Guards against a new WRITE_ENDPOINTS row being added without a matching
    # golden-table entry above (silent capability coverage gap, §03.4's
    # "honest cost").
    covered = {
        ("PUT", "/projects/{id}/merge_requests/{iid}/merge"),
        ("POST", "/projects/{id}/merge_requests"),
        ("POST", "/projects/{id}/merge_requests/{iid}/notes"),
        ("POST", "/projects/{id}/merge_requests/{iid}/discussions"),
        ("POST", "/projects/{id}/merge_requests/{iid}/discussions/{discussion_id}/notes"),
        ("PUT", "/projects/{id}/merge_requests/{iid}"),
        ("POST", "/projects/{id}/pipeline"),
    }
    actual = {(ep.method, ep.template) for ep in WRITE_ENDPOINTS}
    assert actual == covered


# --- end-to-end via decide(): the invariant holds on both channels --------


def test_e2e_git_tag_push_denied_r4(cfg):
    req = ProxyRequest(
        channel="git",
        project="group/proj",
        ref_commands=[RefCommand(ZERO, SHA, "refs/tags/claude/v1")],
    )
    d = decide(req, StateView(), cfg)
    assert not d.allow and d.rule == "R4"


def test_e2e_git_branch_delete_denied_r4(cfg):
    req = ProxyRequest(
        channel="git",
        project="group/proj",
        ref_commands=[RefCommand(SHA, ZERO, "refs/heads/claude/feature")],
    )
    d = decide(req, StateView(), cfg)
    assert not d.allow and d.rule == "R4"


def test_e2e_api_merge_endpoint_denied_r4(cfg):
    d = decide(_api("PUT", "/projects/group%2Fproj/merge_requests/7/merge"), StateView(), cfg)
    assert not d.allow and d.rule == "R4"


def test_e2e_api_state_event_merge_alias_denied_r4(cfg):
    req = _api("PUT", "/projects/group%2Fproj/merge_requests/7", state_event="merge")
    req.mr_owner_ok = True
    d = decide(req, StateView(), cfg)
    assert not d.allow and d.rule == "R4"


def test_e2e_capability_layer_denies_even_without_endpoint_checks(cfg):
    """Proves the invariant is structural, not just a lucky consequence of the
    existing ``always_deny``/``not_merge_intent`` checks (§03.4): a
    hypothetical endpoint row that declares the merge capability but has *no*
    checks at all is still denied, because the capability layer runs before
    ``ep.checks`` in ``policy._decide_api``.
    """
    hypothetical_row = WriteEndpoint(
        method="PUT",
        template="/projects/{id}/merge_requests/{iid}/merge",
        checks=(),  # no checks whatsoever — the old-style defense is gone
        rule="R4",
        kind=EndpointKind.MERGE,
        capabilities=frozenset({Capability.MERGES}),
    )
    req = _api("PUT", "/projects/group%2Fproj/merge_requests/7/merge")
    req.endpoint = hypothetical_row
    d = decide(req, StateView(), cfg)
    assert not d.allow and d.rule == "R4"


def test_e2e_capability_layer_denies_git_even_if_check_ref_logic_is_bypassed(cfg):
    """Mirrors the API-side proof above for git: ``_decide_git`` runs the
    capability check for every ref-command *before* the ``check_ref`` loop, so
    a tag push is denied at that first pass — independent of whatever
    ``check_ref`` itself would separately decide.
    """
    req = ProxyRequest(
        channel="git",
        project="group/proj",
        ref_commands=[RefCommand(ZERO, SHA, "refs/tags/claude/v1")],
    )
    d = decide(req, StateView(), cfg)
    assert not d.allow and d.rule == "R4"
    assert "forbidden capability" in d.reason  # came from the capability layer, not check_ref
