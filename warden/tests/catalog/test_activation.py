"""A host's effective actions × Catalog -> the effective table: fail-closed
validation of every activation rule.
"""

from __future__ import annotations

import pytest

from warden.guards.git.actions import DEFAULT as GIT_DEFAULT
from warden.guards.gitlab_api.catalog import write_endpoints as entries_mod
from warden.guards.gitlab_api.catalog.activation import EffectiveTable, build_effective_table
from warden.guards.gitlab_api.catalog.errors import CatalogConfigError
from warden.guards.gitlab_api.catalog.model import EndpointKind, Recognizer, ScopeKind
from warden.guards.gitlab_api.catalog.write_endpoints import DEFAULT_ENABLED, WRITE_ENDPOINTS

_DEFAULT_ACTION_IDS = tuple(sorted(a.id for a in GIT_DEFAULT))

# --- default behaviour (behaviour preservation across the actions rebuild) --


def test_default_actions_activate_exactly_the_default_recognizer_set():
    # repo.branch.create is default-on in the new vocabulary (unlike the old
    # REST-only DEFAULT_ENABLED), so its recognizer joins the activated set.
    table = build_effective_table(_DEFAULT_ACTION_IDS)
    assert {e.id for e in table.entries} == DEFAULT_ENABLED | {"branch.create"}
    assert all(v == "default" for v in table.enabled_via.values())


def test_empty_actions_activates_nothing():
    table = build_effective_table(())
    assert table.entries == ()
    assert table.enabled_via == {}


def test_mr_comment_folds_to_its_three_recognizers():
    table = build_effective_table(("project.mr.comment",))
    assert {e.id for e in table.entries} == {"mr.note", "mr.discussion", "mr.discussion_reply"}


def test_actions_without_mr_create_do_not_match_mr_create_path():
    # default-deny: mr.create's recognizer must be entirely absent, not just
    # "present but inactive" — match_endpoint has nothing to find.
    table = build_effective_table(("project.mr.comment",))
    assert "mr.create" not in {e.id for e in table.entries}


def test_actions_list_can_add_a_non_default_entry():
    table = build_effective_table(_DEFAULT_ACTION_IDS + ("project.issue.create",))
    ids = {e.id for e in table.entries}
    assert "issue.create" in ids
    assert table.enabled_via["issue.create"] == "config:project.issue.create"
    assert all(table.enabled_via[i] == "default" for i in DEFAULT_ENABLED if i in table.enabled_via)


def test_actions_list_can_shrink_the_default_set():
    reduced = tuple(a for a in _DEFAULT_ACTION_IDS if a != "project.ci.trigger")
    table = build_effective_table(reduced)
    assert "pipeline.trigger" not in {e.id for e in table.entries}


def test_duplicate_action_in_list_is_tolerated():
    table = build_effective_table(("project.mr.create", "project.mr.create"))
    assert [e.id for e in table.entries] == ["mr.create"]


def test_ids_with_no_rest_recognizer_are_ignored_here():
    # repo.read/repo.branch.push gate nothing in the REST guard's table (the
    # git guard's own action gate consumes them) — they must never raise nor
    # add a row.
    table = build_effective_table(("repo.read", "repo.branch.push", "project.mr.create"))
    assert {e.id for e in table.entries} == {"mr.create"}


def test_read_table_is_untouched_by_actions():
    # The REST-Read table (read_endpoints.py) is not action-addressable at
    # all — build_effective_table only ever touches WRITE_ENDPOINTS, never
    # the read catalog, regardless of whether repo.read is present.
    from warden.guards.gitlab_api.catalog.read_endpoints import READ_ENDPOINTS

    with_read = build_effective_table(("repo.read", "project.mr.create"))
    without_read = build_effective_table(("project.mr.create",))
    assert {e.id for e in with_read.entries} == {e.id for e in without_read.entries}
    assert READ_ENDPOINTS  # sanity: the read table exists and is unrelated


# --- fail-closed validation --------------------------------------------------


def test_unknown_action_id_raises():
    with pytest.raises(CatalogConfigError, match="unknown action id"):
        build_effective_table(("no.such.entry",))


def test_enabling_a_forbidden_capability_entry_raises(monkeypatch):
    # No shipped catalog entry actually declares a FORBIDDEN capability
    # (test_capabilities.py pins that down) — this proves the *validation
    # branch itself* fires correctly for a hypothetical one that did, by
    # substituting a fake catalog + a fake action mapping to it.
    from warden.core.capabilities import Capability

    forbidden_entry = Recognizer(
        id="hypothetical.forbidden",
        method="POST",
        template="/projects/{id}/whatever",
        scope_kind=ScopeKind.QUOTA_BY_KIND,
        rule="R4",
        kind=EndpointKind.ISSUE,
        capabilities=frozenset({Capability.MERGES}),
    )
    monkeypatch.setattr(entries_mod, "WRITE_ENDPOINTS", WRITE_ENDPOINTS + (forbidden_entry,))
    # activation.py imports WRITE_ENDPOINTS by name at module load time —
    # patch its own binding too, exactly as a monkeypatch of a
    # "from x import y" name.
    import warden.guards.gitlab_api.catalog.activation as activation_mod

    monkeypatch.setattr(activation_mod, "WRITE_ENDPOINTS", entries_mod.WRITE_ENDPOINTS)

    import warden.guards.git.actions as git_actions_mod
    import warden.guards.gitlab_api.actions as actions_mod

    fake_bridge = dict(actions_mod._BRIDGE_10_03_RECOGNIZER_TO_ACTION)
    fake_bridge["hypothetical.forbidden"] = "hypothetical.action"
    monkeypatch.setattr(actions_mod, "_BRIDGE_10_03_RECOGNIZER_TO_ACTION", fake_bridge)

    fake_by_id = dict(git_actions_mod.by_id)
    fake_by_id["hypothetical.action"] = git_actions_mod.Action(
        "hypothetical.action", git_actions_mod.Criticality.WRITE
    )
    monkeypatch.setattr(git_actions_mod, "by_id", fake_by_id)

    with pytest.raises(CatalogConfigError, match="forbidden capabilities"):
        build_effective_table(("hypothetical.action",))


def test_effective_table_is_a_frozen_dataclass():
    table = EffectiveTable(entries=(), enabled_via={})
    with pytest.raises(Exception):
        table.entries = ()  # type: ignore[misc]
