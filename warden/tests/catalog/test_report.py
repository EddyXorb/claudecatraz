"""``endpoint_table_report`` (§04.3, revisited by §07 Punkt 7): the ``/policy``
admin-route JSON shape must stay stable across the Recognizer unification —
the CLI (``catraz doctor``/``allow-endpoint``) parses this dict by key
(``id``, ``active``, ``enabled_via`` — see ``tests/cli/test_doctor_endpoints.py``
in the sibling ``catraz`` package), so a structural regression here would be
a silent CLI break.
"""

from __future__ import annotations

from warden.core.config import Config
from warden.guards.gitlab_api.catalog.report import endpoint_table_report

_EXPECTED_ROW_KEYS = {
    "id",
    "method",
    "template",
    "kind",
    "rule",
    "capabilities",
    "default",
    "active",
    "enabled_via",
    "decision_fields",
}


def _cfg() -> Config:
    return Config(allowed_projects=("group/proj",))


def test_report_top_level_shape():
    report = endpoint_table_report(_cfg())
    assert set(report.keys()) == {"catalog", "builtin_deny"}
    assert report["builtin_deny"] == ["mr.merge"]


def test_report_every_row_has_the_stable_key_set():
    report = endpoint_table_report(_cfg())
    for row in report["catalog"]:
        assert set(row.keys()) == _EXPECTED_ROW_KEYS


def test_report_mr_create_row_values():
    report = endpoint_table_report(_cfg())
    row = next(r for r in report["catalog"] if r["id"] == "mr.create")
    assert row["method"] == "POST"
    assert row["template"] == "/projects/{id}/merge_requests"
    assert row["kind"] == "mr"
    assert row["rule"] == "R3"
    assert row["capabilities"] == []
    assert row["default"] is True
    assert row["active"] is True
    assert row["enabled_via"] == "default"
    assert row["decision_fields"] == [{"name": "source_branch", "location": "body"}]


def test_report_branch_create_row_not_active_by_default():
    report = endpoint_table_report(_cfg())
    row = next(r for r in report["catalog"] if r["id"] == "branch.create")
    assert row["default"] is False
    assert row["active"] is False
    assert row["enabled_via"] is None
    assert row["capabilities"] == ["creates_ref"]


def test_report_mr_update_row_has_no_static_merges_capability():
    # api_capabilities() adds `merges` only field-dependently (state_event) —
    # the static row itself must stay empty (§07 Punkt 7: no leftover
    # field_not_equals check either, but that's a catalog-structure concern,
    # covered in test_recognizer.py; this pins down the *reported* shape).
    report = endpoint_table_report(_cfg())
    row = next(r for r in report["catalog"] if r["id"] == "mr.update")
    assert row["capabilities"] == []
    assert row["decision_fields"] == [{"name": "state_event", "location": "body"}]
