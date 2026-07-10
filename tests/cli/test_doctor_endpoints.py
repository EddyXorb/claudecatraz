"""catraz doctor's endpoint-catalog section (per-host report), in the style
of tests/cli/test_doctor_gitlab.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from catraz import doctor
from catraz.admin_client import AdminUnreachable


def _write_endpoint(root: Path, host: str = "gitlab.com") -> None:
    """A configured endpoint is what makes check_endpoints reach for /policy."""
    config_dir = root / ".catraz" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "warden.toml").write_text(
        f'[[git.endpoint]]\nhost = "{host}"\ntype = "gitlab"\n'
    )


def _action(id_: str, criticality: str, default: bool, active: bool) -> dict[str, object]:
    return {"id": id_, "criticality": criticality, "default": default, "active": active}


_CATALOG = [
    {
        "guard": "gitlab",
        "id": "mr.create",
        "actions": [_action("project.mr.create", "WRITE", True, True)],
        "quota_kind": "mr",
    },
    {
        "guard": "gitlab",
        "id": "mr.note",
        "actions": [_action("project.mr.comment", "WRITE", True, True)],
        "quota_kind": "mr_note",
    },
    {
        "guard": "gitlab",
        "id": "branch.create",
        "actions": [_action("repo.branch.create", "WRITE", True, True)],
        "quota_kind": "branch_create",
    },
    {
        "guard": "gitlab",
        "id": "issue.create",
        "actions": [_action("project.issue.create", "WRITE", False, False)],
        "quota_kind": "issue_create",
    },
    {
        "guard": "gitlab",
        "id": "mr.merge",
        "actions": [_action("project.mr.merge", "IRREVERSIBLE", False, False)],
        "quota_kind": "mr_merge",
    },
]

_REPORT = {
    "hosts": {
        "gitlab.example": {
            "actions": ["repo.read", "repo.branch.create", "project.mr.create"],
            "catalog": _CATALOG,
            "denials": ["project.mr.merge"],
        },
    },
}


def test_no_endpoint_short_circuits(tmp_path: Path) -> None:
    f = doctor.Findings()
    doctor.check_endpoints(tmp_path, {}, f)
    assert any(i[0] == doctor.OK and "not applicable" in i[2] for i in f.items)


def test_admin_unreachable_warns_and_does_not_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_endpoint(tmp_path)

    def _raise(root: Path) -> dict[str, object]:
        raise AdminUnreachable("admin socket not found")

    monkeypatch.setattr("catraz.endpoints.fetch_policy_report", _raise)
    f = doctor.Findings()
    doctor.check_endpoints(tmp_path, {}, f)
    assert any(i[0] == doctor.WARN for i in f.items)
    assert not any(i[0] == doctor.BAD for i in f.items)


def test_no_hosts_configured_is_ok_not_warn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_endpoint(tmp_path)
    monkeypatch.setattr("catraz.endpoints.fetch_policy_report", lambda root: {"hosts": {}})
    f = doctor.Findings()
    doctor.check_endpoints(tmp_path, {}, f)
    assert any(i[0] == doctor.OK and "no hosts configured" in i[2] for i in f.items)
    assert not any(i[0] == doctor.BAD for i in f.items)


def test_reachable_reports_active_and_inactive_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_endpoint(tmp_path, "gitlab.example")
    monkeypatch.setattr("catraz.endpoints.fetch_policy_report", lambda root: _REPORT)
    f = doctor.Findings()
    doctor.check_endpoints(tmp_path, {}, f)
    msgs = [i[2] for i in f.items]
    assert any("gitlab.example" in m and "project.mr.create" in m and "3 active" in m for m in msgs)
    assert any("project.issue.create" in m and "not enabled" in m for m in msgs)
    assert any("project.mr.merge" in m and "compiled-in denial" in m for m in msgs)
    assert not any(i[0] == doctor.BAD for i in f.items)


def test_two_hosts_get_independent_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    two_hosts_report = {
        "hosts": {
            "full.example": {"actions": [], "catalog": _CATALOG, "denials": ["project.mr.merge"]},
            "review-only.example": {
                "actions": ["project.mr.comment"],
                "catalog": [
                    {
                        "guard": "gitlab",
                        "id": "mr.create",
                        "actions": [_action("project.mr.create", "WRITE", True, False)],
                        "quota_kind": "mr",
                    },
                    {
                        "guard": "gitlab",
                        "id": "mr.note",
                        "actions": [_action("project.mr.comment", "WRITE", True, True)],
                        "quota_kind": "mr_note",
                    },
                ],
                "denials": [],
            },
        },
    }
    _write_endpoint(tmp_path)
    monkeypatch.setattr("catraz.endpoints.fetch_policy_report", lambda root: two_hosts_report)
    f = doctor.Findings()
    doctor.check_endpoints(tmp_path, {}, f)
    msgs = [i[2] for i in f.items]
    assert any(m.startswith("full.example:") for m in msgs)
    assert any(m.startswith("review-only.example:") for m in msgs)


def test_endpoints_is_in_doctor_sections() -> None:
    assert "endpoints" in doctor.DOCTOR_SECTIONS


def test_endpoints_is_not_a_gating_security_section() -> None:
    # Informational only — activating an extra catalog entry is a deliberate
    # choice, not something `catraz up` should ever fail on.
    assert "endpoints" not in doctor.SECURITY_SECTIONS
