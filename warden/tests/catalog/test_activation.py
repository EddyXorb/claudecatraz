"""Config × Catalog → the effective table (§04.2/04.3): fail-closed
validation of every activation rule, and the narrowing-only override
mechanism.
"""

from __future__ import annotations

import pytest

from warden.core.config import Config
from warden.guards.gitlab_api.catalog import entries as entries_mod
from warden.guards.gitlab_api.catalog.activation import EffectiveTable, build_effective_table
from warden.guards.gitlab_api.catalog.config_parse import EndpointActivation
from warden.guards.gitlab_api.catalog.entries import CATALOG, DEFAULT_ENABLED
from warden.guards.gitlab_api.catalog.errors import CatalogConfigError
from warden.guards.gitlab_api.catalog.model import CatalogEntry, EndpointKind


@pytest.fixture
def cfg() -> Config:
    return Config(allowed_projects=("group/proj",), read_token="r", write_token="w")


# --- default behaviour (§04.3 behaviour preservation) -----------------------


def test_absent_section_activates_exactly_the_default_set(cfg):
    table = build_effective_table(cfg, EndpointActivation())
    assert {e.id for e in table.entries} == DEFAULT_ENABLED
    assert all(v == "default" for v in table.enabled_via.values())


def test_explicit_empty_enable_list_activates_nothing(cfg):
    table = build_effective_table(cfg, EndpointActivation(enable=()))
    assert table.entries == ()
    assert table.enabled_via == {}


def test_enable_list_can_add_a_non_default_entry(cfg):
    table = build_effective_table(
        cfg, EndpointActivation(enable=tuple(DEFAULT_ENABLED) + ("branch.create",))
    )
    ids = {e.id for e in table.entries}
    assert ids == DEFAULT_ENABLED | {"branch.create"}
    assert table.enabled_via["branch.create"] == "config:branch.create"
    assert all(table.enabled_via[i] == "default" for i in DEFAULT_ENABLED)


def test_enable_list_can_shrink_the_default_set(cfg):
    reduced = tuple(DEFAULT_ENABLED - {"pipeline.trigger"})
    table = build_effective_table(cfg, EndpointActivation(enable=reduced))
    assert "pipeline.trigger" not in {e.id for e in table.entries}


def test_duplicate_id_in_enable_list_is_tolerated(cfg):
    table = build_effective_table(cfg, EndpointActivation(enable=("mr.create", "mr.create")))
    assert [e.id for e in table.entries] == ["mr.create"]


# --- fail-closed validation (§04.3) -----------------------------------------


def test_unknown_id_in_enable_raises(cfg):
    with pytest.raises(CatalogConfigError, match="unknown catalog id"):
        build_effective_table(cfg, EndpointActivation(enable=("no.such.entry",)))


def test_override_for_unknown_id_raises(cfg):
    with pytest.raises(CatalogConfigError, match="unknown catalog id"):
        build_effective_table(
            cfg,
            EndpointActivation(
                enable=tuple(DEFAULT_ENABLED), overrides={"no.such.entry": {"x": "y"}}
            ),
        )


def test_override_for_non_enabled_entry_raises(cfg):
    with pytest.raises(CatalogConfigError, match="not in \\[api.endpoints\\].enable"):
        build_effective_table(
            cfg,
            EndpointActivation(
                enable=tuple(DEFAULT_ENABLED),  # branch.create NOT enabled
                overrides={"branch.create": {"branch_prefix": "claude/x-"}},
            ),
        )


def test_override_with_unknown_key_raises(cfg):
    with pytest.raises(CatalogConfigError, match="no overridable parameter"):
        build_effective_table(
            cfg,
            EndpointActivation(
                enable=tuple(DEFAULT_ENABLED) + ("branch.create",),
                overrides={"branch.create": {"not_a_real_key": "x"}},
            ),
        )


def test_override_that_widens_raises(cfg):
    # "main" is not within cfg.branch_prefixes ("claude/") — widening, refused.
    with pytest.raises(CatalogConfigError, match="does not narrow"):
        build_effective_table(
            cfg,
            EndpointActivation(
                enable=tuple(DEFAULT_ENABLED) + ("branch.create",),
                overrides={"branch.create": {"branch_prefix": "main"}},
            ),
        )


def test_override_that_narrows_is_applied_and_enforced(cfg):
    table = build_effective_table(
        cfg,
        EndpointActivation(
            enable=tuple(DEFAULT_ENABLED) + ("branch.create",),
            overrides={"branch.create": {"branch_prefix": "claude/only-this-"}},
        ),
    )
    entry = next(e for e in table.entries if e.id == "branch.create")
    check = entry.checks[0]
    from warden.core.model import StateView
    from warden.guards.gitlab_api.intent import ApiIntent

    req_ok = ApiIntent(
        _project="group/proj", _method="POST",
        path="/projects/group%2Fproj/repository/branches",
        fields={"branch": "claude/only-this-x"},
    )
    assert check(req_ok, StateView(), cfg) is None  # narrower prefix satisfied

    req_too_wide = ApiIntent(
        _project="group/proj", _method="POST",
        path="/projects/group%2Fproj/repository/branches",
        fields={"branch": "claude/other"},  # inside the general namespace, NOT the override
    )
    d = check(req_too_wide, StateView(), cfg)
    assert d is not None and not d.allow


def test_enabling_a_forbidden_capability_entry_raises(cfg, monkeypatch):
    # No shipped catalog entry actually declares a FORBIDDEN capability
    # (test_capabilities.py pins that down) — this proves the *validation
    # branch itself* fires correctly for a hypothetical one that did, by
    # substituting a fake catalog.
    from warden.core.capabilities import Capability

    forbidden_entry = CatalogEntry(
        id="hypothetical.forbidden",
        method="POST",
        template="/projects/{id}/whatever",
        checks=(),
        rule="R4",
        kind=EndpointKind.ISSUE,
        capabilities=frozenset({Capability.MERGES}),
    )
    monkeypatch.setattr(entries_mod, "CATALOG", CATALOG + (forbidden_entry,))
    # activation.py imported CATALOG by name at module load time — patch its
    # own binding too, exactly as a monkeypatch of a "from x import y" name.
    import warden.guards.gitlab_api.catalog.activation as activation_mod

    monkeypatch.setattr(activation_mod, "CATALOG", entries_mod.CATALOG)

    with pytest.raises(CatalogConfigError, match="forbidden capabilities"):
        build_effective_table(cfg, EndpointActivation(enable=("hypothetical.forbidden",)))


def test_effective_table_is_a_frozen_dataclass():
    table = EffectiveTable(entries=(), enabled_via={})
    with pytest.raises(Exception):
        table.entries = ()  # type: ignore[misc]
