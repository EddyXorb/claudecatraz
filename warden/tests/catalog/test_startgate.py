"""The startgate (§04.4): every activated entry's deny-probes, plus the
built-in global probes, run against the real, pure ``policy.decide`` before
the warden would ever open a port.
"""

from __future__ import annotations

import pytest

import warden.guards.gitlab_api.catalog.probes as probes_mod
from warden.core.config import Config
from warden.guards.gitlab_api.catalog.activation import EffectiveTable, build_effective_table
from warden.guards.gitlab_api.catalog.entries import CATALOG
from warden.guards.gitlab_api.catalog.errors import StartgateFailure
from warden.guards.gitlab_api.catalog.model import (
    PROBE_PROJECT_PATH,
    CatalogEntry,
    DenyProbe,
    EndpointKind,
)
from warden.guards.gitlab_api.catalog.startgate import run_startgate


@pytest.fixture
def cfg() -> Config:
    return Config(allowed_projects=("group/proj",), read_token="r", write_token="w")


def test_default_table_passes_the_startgate(cfg):
    table = build_effective_table(cfg, None)
    run_startgate(cfg, table)  # must not raise


def test_every_catalog_entry_individually_passes_its_own_probes(cfg):
    # Including the two extra, non-default entries — a probe must hold
    # whether or not the entry ships in the default set.
    for entry in CATALOG:
        table = EffectiveTable(entries=(entry,), enabled_via={entry.id: "config:" + entry.id})
        run_startgate(cfg, table)  # must not raise for any single entry


def test_full_catalog_activated_still_passes(cfg):
    table = build_effective_table(cfg, tuple(e.id for e in CATALOG))
    run_startgate(cfg, table)


def test_empty_table_still_runs_builtin_global_probes(cfg):
    # No entries activated at all — the merge invariant's global probes must
    # still be exercised (they are not tied to any catalog entry).
    table = EffectiveTable(entries=(), enabled_via={})
    run_startgate(cfg, table)  # must not raise


def test_a_probe_that_would_be_allowed_raises_startgate_failure(cfg, monkeypatch):
    bad_entry = CatalogEntry(
        id="bad.entry",
        method="POST",
        template="/projects/{id}/issues",
        checks=(),  # nothing gates this — the probe below is actually allowed
        rule="R3",
        kind=EndpointKind.ISSUE,
    )
    monkeypatch.setitem(
        probes_mod.ENTRY_DENY_PROBES,
        "bad.entry",
        (
            DenyProbe(
                description="misconfigured probe: this request is actually allowed",
                method="POST",
                path=f"/projects/{PROBE_PROJECT_PATH}/issues",
                fields={"title": "x"},
            ),
        ),
    )
    table = EffectiveTable(entries=(bad_entry,), enabled_via={"bad.entry": "config:bad.entry"})
    with pytest.raises(StartgateFailure, match="bad.entry"):
        run_startgate(cfg, table)


def test_startgate_failure_names_the_probe_description(cfg, monkeypatch):
    bad_entry = CatalogEntry(
        id="bad.entry",
        method="POST",
        template="/projects/{id}/issues",
        checks=(),
        rule="R3",
        kind=EndpointKind.ISSUE,
    )
    monkeypatch.setitem(
        probes_mod.ENTRY_DENY_PROBES,
        "bad.entry",
        (
            DenyProbe(
                description="a very specific probe description",
                method="POST",
                path=f"/projects/{PROBE_PROJECT_PATH}/issues",
            ),
        ),
    )
    table = EffectiveTable(entries=(bad_entry,), enabled_via={"bad.entry": "config:bad.entry"})
    with pytest.raises(StartgateFailure, match="a very specific probe description"):
        run_startgate(cfg, table)
