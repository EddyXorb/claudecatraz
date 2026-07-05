"""``endpoint_table_report``: the ``/policy`` admin-route JSON shape, per host."""

from __future__ import annotations

from warden.core.config import Config, GitEndpoint
from warden.guards.git.gitlab.report import endpoint_table_report

_HOST = "gitlab.example"


def _cfg() -> Config:
    return Config(
        allowed_projects=("group/proj",),
        git_endpoints=(GitEndpoint(host=_HOST, type="gitlab"),),
    )


def test_report_top_level_shape():
    report = endpoint_table_report(_cfg())
    assert set(report.keys()) == {"hosts"}
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
        assert set(row.keys()) == {"id", "methods", "template", "quota_kind"}


def test_report_mr_create_row_values():
    host_report = endpoint_table_report(_cfg())["hosts"][_HOST]
    row = next(r for r in host_report["catalog"] if r["id"] == "mr.create")
    assert row["methods"] == ["POST"]
    assert row["template"] == "/projects/{id}/merge_requests"
    assert row["quota_kind"] == "mr"


def test_report_merge_row_is_an_ordinary_catalog_row():
    # No more special-cased "builtin_deny" — merge is a named, criticality-denied row.
    host_report = endpoint_table_report(_cfg())["hosts"][_HOST]
    row = next(r for r in host_report["catalog"] if r["id"] == "mr.merge")
    assert row["quota_kind"] == "mr_merge"


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
    assert "project.mr.create" in full["actions"]
    assert review_only["actions"] == ["project.mr.comment"]
