"""Config × Catalog → the effective table (§04.2/04.3): fail-closed
validation of every activation rule.
"""

from __future__ import annotations

import pytest

from warden.core.config import Config
from warden.guards.gitlab_api.catalog import write_endpoints as entries_mod
from warden.guards.gitlab_api.catalog.activation import EffectiveTable, build_effective_table
from warden.guards.gitlab_api.catalog.errors import CatalogConfigError
from warden.guards.gitlab_api.catalog.model import EndpointKind, Recognizer, ScopeKind
from warden.guards.gitlab_api.catalog.write_endpoints import DEFAULT_ENABLED, WRITE_ENDPOINTS


@pytest.fixture
def cfg() -> Config:
    return Config(allowed_projects=("group/proj",), read_token="r", write_token="w")


# --- default behaviour (§04.3 behaviour preservation) -----------------------


def test_absent_section_activates_exactly_the_default_set(cfg):
    table = build_effective_table(cfg, None)
    assert {e.id for e in table.entries} == DEFAULT_ENABLED
    assert all(v == "default" for v in table.enabled_via.values())


def test_explicit_empty_enable_list_activates_nothing(cfg):
    table = build_effective_table(cfg, ())
    assert table.entries == ()
    assert table.enabled_via == {}


def test_enable_list_can_add_a_non_default_entry(cfg):
    table = build_effective_table(cfg, tuple(DEFAULT_ENABLED) + ("branch.create",))
    ids = {e.id for e in table.entries}
    assert ids == DEFAULT_ENABLED | {"branch.create"}
    assert table.enabled_via["branch.create"] == "config:branch.create"
    assert all(table.enabled_via[i] == "default" for i in DEFAULT_ENABLED)


def test_enable_list_can_shrink_the_default_set(cfg):
    reduced = tuple(DEFAULT_ENABLED - {"pipeline.trigger"})
    table = build_effective_table(cfg, reduced)
    assert "pipeline.trigger" not in {e.id for e in table.entries}


def test_duplicate_id_in_enable_list_is_tolerated(cfg):
    table = build_effective_table(cfg, ("mr.create", "mr.create"))
    assert [e.id for e in table.entries] == ["mr.create"]


# --- fail-closed validation (§04.3) -----------------------------------------


def test_unknown_id_in_enable_raises(cfg):
    with pytest.raises(CatalogConfigError, match="unknown catalog id"):
        build_effective_table(cfg, ("no.such.entry",))


def test_enabling_a_forbidden_capability_entry_raises(cfg, monkeypatch):
    # No shipped catalog entry actually declares a FORBIDDEN capability
    # (test_capabilities.py pins that down) — this proves the *validation
    # branch itself* fires correctly for a hypothetical one that did, by
    # substituting a fake catalog.
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
    # activation.py imported CATALOG by name at module load time — patch its
    # own binding too, exactly as a monkeypatch of a "from x import y" name.
    import warden.guards.gitlab_api.catalog.activation as activation_mod

    monkeypatch.setattr(activation_mod, "WRITE_ENDPOINTS", entries_mod.WRITE_ENDPOINTS)

    with pytest.raises(CatalogConfigError, match="forbidden capabilities"):
        build_effective_table(cfg, ("hypothetical.forbidden",))


def test_effective_table_is_a_frozen_dataclass():
    table = EffectiveTable(entries=(), enabled_via={})
    with pytest.raises(Exception):
        table.entries = ()  # type: ignore[misc]
