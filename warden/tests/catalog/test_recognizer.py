"""§07 Punkt 7: the unified Recognizer structure — read and write endpoints
are the same type, reduced to a closed scope vocabulary.

This is the "new safety net" the design doc's Schritt 7 calls for on top of
the pre-existing behaviour tests (test_policy.py/test_capabilities.py/
test_api_proxy.py/redteam), which already pin down that the *decisions*
didn't change. These tests pin down the *structure* itself: every catalog/
read-table row is a plain, introspectable Recognizer — no per-entry function
sneaks back in for branch-namespace/quota-by-kind, and every content-exposure
row genuinely has a classifier.
"""

from __future__ import annotations

from warden.guards.gitlab_api.catalog.entries import CATALOG
from warden.guards.gitlab_api.catalog.model import ReadClass, Recognizer, ScopeKind
from warden.guards.gitlab_api.read_endpoints import READ_RECOGNIZERS

# --- the write catalog: every row is BRANCH_NAMESPACE or QUOTA_BY_KIND -----


def test_every_catalog_row_is_a_recognizer():
    assert all(isinstance(ep, Recognizer) for ep in CATALOG)


def test_every_catalog_row_has_a_write_scope():
    # §07 Punkt 7's closed scope space: no CONTENT_EXPOSURE row belongs in the
    # write catalog — that scope is exclusively the read table's.
    for ep in CATALOG:
        assert ep.scope_kind in (ScopeKind.BRANCH_NAMESPACE, ScopeKind.QUOTA_BY_KIND)
        assert ep.classify is None
        assert ep.kind is not None


def test_branch_namespace_rows_declare_the_expected_namespace_field():
    # Literal-field rows (source_branch/ref/branch) vs. iid-lookup rows
    # (namespace_field=None, resolved via intent.mr_source_ok) — pinned down
    # so a future edit can't silently flip a row from one to the other.
    literal_fields = {
        "mr.create": "source_branch",
        "pipeline.trigger": "ref",
        "branch.create": "branch",
    }
    iid_lookup_ids = {"mr.note", "mr.discussion", "mr.discussion_reply", "mr.update"}
    by_id = {ep.id: ep for ep in CATALOG}
    for entry_id, field in literal_fields.items():
        ep = by_id[entry_id]
        assert ep.scope_kind is ScopeKind.BRANCH_NAMESPACE
        assert ep.namespace_field == field
    for entry_id in iid_lookup_ids:
        ep = by_id[entry_id]
        assert ep.scope_kind is ScopeKind.BRANCH_NAMESPACE
        assert ep.namespace_field is None


def test_issue_create_is_quota_by_kind_with_no_namespace_field():
    ep = next(e for e in CATALOG if e.id == "issue.create")
    assert ep.scope_kind is ScopeKind.QUOTA_BY_KIND
    assert ep.namespace_field is None


def test_mr_update_has_no_leftover_state_event_check():
    # §07 Punkt 7: the former separate "state_event != merge" check is gone —
    # the FORBIDDEN capability layer (api_capabilities' field-dependent MERGES
    # alias) is the only thing standing between state_event=merge and a deny.
    # There is no `checks` attribute left on Recognizer at all to carry one.
    ep = next(e for e in CATALOG if e.id == "mr.update")
    assert not hasattr(ep, "checks")
    assert ep.scope_kind is ScopeKind.BRANCH_NAMESPACE
    assert ep.namespace_field is None


# --- the read table: every row is CONTENT_EXPOSURE with a real classifier --


def test_every_read_recognizer_is_content_exposure_with_a_classifier():
    for ep in READ_RECOGNIZERS:
        assert isinstance(ep, Recognizer)
        assert ep.scope_kind is ScopeKind.CONTENT_EXPOSURE
        assert ep.classify is not None
        assert ep.namespace_field is None
        assert ep.kind is None


def test_read_recognizer_classify_returns_the_closed_read_class_vocabulary():
    for ep in READ_RECOGNIZERS:
        assert ep.classify is not None
        # A representative call per row — snippets/search rows are
        # request-dependent (search) or constant (snippets); either way the
        # return type must be the closed (ReadClass, str) pair, never a Decision.
        from warden.guards.gitlab_api.intent import ApiIntent

        probe = ApiIntent(
            _project="", _method="GET", path=ep.template, fields={"scope": "projects"}
        )
        read_class, reason = ep.classify(probe)
        assert isinstance(read_class, ReadClass)
        assert isinstance(reason, str) and reason
