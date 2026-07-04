"""catraz doctor's endpoint-catalog section (per-host report), in the style
of tests/cli/test_doctor_gitlab.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from catraz import doctor
from catraz.admin_client import AdminUnreachable

_CATALOG = [
    {"id": "mr.create", "active": True, "enabled_via": "default"},
    {"id": "mr.note", "active": True, "enabled_via": "default"},
    {"id": "branch.create", "active": True, "enabled_via": "config:branch.create"},
    {"id": "issue.create", "active": False, "enabled_via": None},
]

_REPORT = {
    "hosts": {
        "gitlab.example": {"actions": ["git.fetch", "git.push", "mr.create"], "catalog": _CATALOG},
    },
}


def test_gitlab_off_short_circuits(tmp_path: Path) -> None:
    f = doctor.Findings()
    doctor.check_endpoints(tmp_path, {"GITLAB_MODE": "off"}, f)
    assert any(i[0] == doctor.OK and "not applicable" in i[2] for i in f.items)


def test_admin_unreachable_warns_and_does_not_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    monkeypatch.setattr("catraz.endpoints.fetch_policy_report", lambda root: {"hosts": {}})
    f = doctor.Findings()
    doctor.check_endpoints(tmp_path, {}, f)
    assert any(i[0] == doctor.OK and "no hosts configured" in i[2] for i in f.items)
    assert not any(i[0] == doctor.BAD for i in f.items)


def test_reachable_reports_active_and_inactive_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("catraz.endpoints.fetch_policy_report", lambda root: _REPORT)
    f = doctor.Findings()
    doctor.check_endpoints(tmp_path, {}, f)
    msgs = [i[2] for i in f.items]
    assert any("gitlab.example" in m and "mr.create" in m and "3 active" in m for m in msgs)
    assert any("branch.create[config:branch.create]" in m for m in msgs)
    assert any("issue.create" in m and "not enabled" in m for m in msgs)
    assert not any(i[0] == doctor.BAD for i in f.items)


def test_two_hosts_get_independent_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    two_hosts_report = {
        "hosts": {
            "full.example": {"actions": [], "catalog": _CATALOG},
            "review-only.example": {
                "actions": [],
                "catalog": [
                    {"id": "mr.create", "active": False, "enabled_via": None},
                    {"id": "mr.note", "active": True, "enabled_via": "default"},
                ],
            },
        },
    }
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
