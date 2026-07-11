"""endpoint_table_report: the /policy admin-route JSON shape, per host.

Walks the actual running guards (AppContext.guards), not a report-only
recomputation, so these tests build a real AppContext via build_context.
"""

from __future__ import annotations

from dataclasses import replace

from warden.context import build_context
from warden.core.audit import AuditLog
from warden.core.config import Config, GitEndpoint
from warden.core.state import State
from warden.guards.git.report import endpoint_table_report

_HOST = "gitlab.example"


def _cfg(**kwargs: object) -> Config:
    return Config(allowed_projects=("group/proj",), **kwargs)  # type: ignore[arg-type]


def _report(cfg: Config) -> dict:
    ctx = build_context(cfg, State(":memory:"), AuditLog("-"))
    return endpoint_table_report(ctx.cfg, ctx.guards)


def test_report_top_level_shape():
    cfg = _cfg(git_endpoints=(GitEndpoint(host=_HOST, type="gitlab"),))
    report = _report(cfg)
    assert set(report.keys()) == {"hosts"}
    assert set(report["hosts"]) == {_HOST}


def test_report_no_configured_hosts_yields_an_empty_hosts_map():
    assert _report(_cfg())["hosts"] == {}


def test_host_report_has_the_stable_key_set():
    host_report = _report(_cfg(git_endpoints=(GitEndpoint(host=_HOST, type="gitlab"),)))["hosts"][
        _HOST
    ]
    assert set(host_report.keys()) == {"actions", "catalog", "denials"}


def test_host_report_actions_reflect_the_effective_set():
    host_report = _report(_cfg(git_endpoints=(GitEndpoint(host=_HOST, type="gitlab"),)))["hosts"][
        _HOST
    ]
    assert "project.mr.create" in host_report["actions"]
    assert "repo.branch.create" in host_report["actions"]


# --- git transport rows appear in the report --------


def test_gitlab_type_host_includes_git_transport_rows():
    host_report = _report(_cfg(git_endpoints=(GitEndpoint(host=_HOST, type="gitlab"),)))["hosts"][
        _HOST
    ]
    rows_by_id = {row["id"]: row for row in host_report["catalog"]}
    assert "git.read" in rows_by_id and "git.receive_pack" in rows_by_id
    assert rows_by_id["git.read"]["guard"] == "transport"
    action_ids = {a["id"] for a in rows_by_id["git.read"]["actions"]}
    assert action_ids == {"repo.read"}


def test_gitlab_type_host_also_includes_gitlab_rows():
    host_report = _report(_cfg(git_endpoints=(GitEndpoint(host=_HOST, type="gitlab"),)))["hosts"][
        _HOST
    ]
    rows_by_id = {row["id"]: row for row in host_report["catalog"]}
    assert "mr.create" in rows_by_id
    assert rows_by_id["mr.create"]["guard"] == "gitlab"


def test_plain_type_host_has_only_transport_rows():
    host_report = _report(_cfg(git_endpoints=(GitEndpoint(host=_HOST, type="plain"),)))["hosts"][
        _HOST
    ]
    guards_present = {row["guard"] for row in host_report["catalog"]}
    assert guards_present == {"transport"}


# --- never-class rows: named denials, not a hardcoded builtin_deny string ----


def test_merge_row_shows_its_action_as_a_named_denial():
    host_report = _report(_cfg(git_endpoints=(GitEndpoint(host=_HOST, type="gitlab"),)))["hosts"][
        _HOST
    ]
    assert "project.mr.merge" in host_report["denials"]
    rows_by_id = {row["id"]: row for row in host_report["catalog"]}
    merge_action = next(
        a for a in rows_by_id["mr.merge"]["actions"] if a["id"] == "project.mr.merge"
    )
    assert merge_action["criticality"] == "IRREVERSIBLE"
    assert merge_action["active"] is False


def test_git_receive_pack_row_names_branch_delete_and_tag_denials():
    host_report = _report(_cfg(git_endpoints=(GitEndpoint(host=_HOST, type="gitlab"),)))["hosts"][
        _HOST
    ]
    assert {"repo.branch.delete", "repo.tag.create", "repo.tag.delete"} <= set(
        host_report["denials"]
    )


def test_denied_action_is_inactive_even_if_explicitly_configured():
    # A misconfigured host listing every action id still reports those as
    # inactive: the criticality gate always denies them, regardless of config.
    cfg = _cfg(
        git_endpoints=(
            GitEndpoint(
                host=_HOST,
                type="gitlab",
                actions=("repo.read", "repo.branch.delete", "project.mr.merge"),
            ),
        )
    )
    host_report = _report(cfg)["hosts"][_HOST]
    rows_by_id = {row["id"]: row for row in host_report["catalog"]}
    delete_action = next(
        a for a in rows_by_id["git.receive_pack"]["actions"] if a["id"] == "repo.branch.delete"
    )
    assert delete_action["active"] is False


# --- quota_kind: per action, gitlab actions only ------------------------------


def test_quota_kind_present_on_gitlab_actions_absent_on_transport_actions():
    host_report = _report(_cfg(git_endpoints=(GitEndpoint(host=_HOST, type="gitlab"),)))["hosts"][
        _HOST
    ]
    rows_by_id = {row["id"]: row for row in host_report["catalog"]}
    mr_create_action = rows_by_id["mr.create"]["actions"][0]
    assert mr_create_action["id"] == "project.mr.create"
    assert mr_create_action["quota_kind"] == "mr"
    git_read_action = rows_by_id["git.read"]["actions"][0]
    assert git_read_action["quota_kind"] is None


def test_quota_kind_differs_by_action_within_a_multi_action_row():
    # mr.update's single row recognizes to edit/close/merge depending on
    # state_event — quota kind depends on which action is recognized.
    host_report = _report(_cfg(git_endpoints=(GitEndpoint(host=_HOST, type="gitlab"),)))["hosts"][
        _HOST
    ]
    rows_by_id = {row["id"]: row for row in host_report["catalog"]}
    actions_by_id = {a["id"]: a for a in rows_by_id["mr.update"]["actions"]}
    assert actions_by_id["project.mr.edit"]["quota_kind"] == "mr_update"
    assert actions_by_id["project.mr.close"]["quota_kind"] == "mr_update"
    assert actions_by_id["project.mr.merge"]["quota_kind"] is None


# --- per-host activation differs when two hosts have different actions -------


def test_two_hosts_with_different_actions_get_independent_sections():
    cfg = _cfg(
        git_endpoints=(
            GitEndpoint(host="full.example", type="gitlab"),
            GitEndpoint(
                host="review-only.example",
                type="gitlab",
                actions=("repo.read", "project.read", "project.mr.comment"),
            ),
        )
    )
    report = _report(cfg)
    full = report["hosts"]["full.example"]
    review_only = report["hosts"]["review-only.example"]
    assert "project.mr.create" in full["actions"]
    assert set(review_only["actions"]) == {"repo.read", "project.read", "project.mr.comment"}

    full_mr_create = next(row for row in full["catalog"] if row["id"] == "mr.create")
    review_mr_create = next(row for row in review_only["catalog"] if row["id"] == "mr.create")
    assert full_mr_create["actions"][0]["active"] is True
    assert review_mr_create["actions"][0]["active"] is False


def test_activation_config_replay_via_replace():
    # Same activation-differs behavior, exercised through `replace` on an
    # existing endpoint (mirrors how `test_app.py` drives it).
    base = _cfg(git_endpoints=(GitEndpoint(host=_HOST, type="gitlab"),))
    endpoint = base.git_endpoints[0]
    activated = replace(
        base,
        git_endpoints=(replace(endpoint, actions=("project.mr.create", "project.issue.create")),),
    )
    host_report = _report(activated)["hosts"][_HOST]
    assert set(host_report["actions"]) == {"project.mr.create", "project.issue.create"}
