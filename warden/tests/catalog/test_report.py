"""``endpoint_table_report``: the ``/policy`` admin-route JSON shape must stay
stable, per host — the CLI (``catraz doctor``) parses this dict by key
(``hosts``, ``id``, ``active``, ``enabled_via`` — see
``tests/cli/test_doctor_endpoints.py`` in the sibling ``catraz`` package), so
a structural regression here would be a silent CLI break.
"""

from __future__ import annotations

from warden.core.config import Config, GitEndpoint
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

_HOST = "gitlab.example"


def _cfg() -> Config:
    return Config(
        allowed_projects=("group/proj",),
        git_endpoints=(GitEndpoint(host=_HOST, type="gitlab"),),
    )


def test_report_top_level_shape():
    report = endpoint_table_report(_cfg())
    assert set(report.keys()) == {"hosts", "builtin_deny"}
    assert report["builtin_deny"] == ["mr.merge"]
    assert set(report["hosts"]) == {_HOST}


def test_report_no_configured_hosts_yields_an_empty_hosts_map():
    report = endpoint_table_report(Config(allowed_projects=("group/proj",)))
    assert report["hosts"] == {}


def test_host_report_has_actions_and_catalog_keys():
    host_report = endpoint_table_report(_cfg())["hosts"][_HOST]
    assert set(host_report.keys()) == {"actions", "catalog"}
    assert "project.mr.create" in host_report["actions"]


def test_report_every_row_has_the_stable_key_set():
    host_report = endpoint_table_report(_cfg())["hosts"][_HOST]
    for row in host_report["catalog"]:
        assert set(row.keys()) == _EXPECTED_ROW_KEYS


def test_report_mr_create_row_values():
    host_report = endpoint_table_report(_cfg())["hosts"][_HOST]
    row = next(r for r in host_report["catalog"] if r["id"] == "mr.create")
    assert row["method"] == "POST"
    assert row["template"] == "/projects/{id}/merge_requests"
    assert row["kind"] == "mr"
    assert row["rule"] == "R3"
    assert row["capabilities"] == []
    assert row["default"] is True
    assert row["active"] is True
    assert row["enabled_via"] == "default"
    assert row["decision_fields"] == [{"name": "source_branch", "location": "body"}]


def test_report_branch_create_row_is_active_by_default():
    # repo.branch.create is default-on in the new vocabulary (it covers both
    # the git-push-create-branch wire and this REST endpoint with one knob).
    host_report = endpoint_table_report(_cfg())["hosts"][_HOST]
    row = next(r for r in host_report["catalog"] if r["id"] == "branch.create")
    assert row["default"] is False  # DEFAULT_ENABLED (the REST-only marker) excludes it
    assert row["active"] is True
    assert row["enabled_via"] == "default"
    assert row["capabilities"] == ["creates_ref"]


def test_report_mr_update_row_has_no_static_merges_capability():
    # api_capabilities() adds `merges` only field-dependently (state_event) —
    # the static row itself must stay empty (no leftover field_not_equals
    # check either, but that's a catalog-structure concern, covered in
    # test_recognizer.py; this pins down the *reported* shape).
    host_report = endpoint_table_report(_cfg())["hosts"][_HOST]
    row = next(r for r in host_report["catalog"] if r["id"] == "mr.update")
    assert row["capabilities"] == []
    assert row["decision_fields"] == [{"name": "state_event", "location": "body"}]


def test_two_hosts_with_different_actions_get_independent_sections():
    cfg = Config(
        allowed_projects=("group/proj",),
        git_endpoints=(
            GitEndpoint(host="full.example", type="gitlab"),
            GitEndpoint(host="review-only.example", type="gitlab", actions=("project.mr.comment",)),
        ),
    )
    report = endpoint_table_report(cfg)
    full = report["hosts"]["full.example"]
    review_only = report["hosts"]["review-only.example"]

    def _active(host_report: dict, endpoint_id: str) -> bool:
        return next(r for r in host_report["catalog"] if r["id"] == endpoint_id)["active"]

    assert _active(full, "mr.create") is True
    assert _active(review_only, "mr.create") is False
    assert _active(review_only, "mr.note") is True
