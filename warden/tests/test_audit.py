"""audit.py (W11, §6.8): redaction-by-allowlist and the single-writer drain.

The crown-jewel invariant: secrets (tokens, Authorization) are dropped *by
construction* — the log is an allowlist of fields, never a blocklist. If that
ever regresses, a credential could land on disk.
"""

from __future__ import annotations

import json

from warden.core.audit import _ALLOWED_FIELDS, AUDIT_SCHEMA_VERSION, AuditLog, build_event, redact
from warden.core.model import Decision, StateView


def test_schema_v3_event_carries_guard_not_channel():
    """§06-migration.md Schritt 6 (F11): the JSONL field is ``guard`` now, the
    line is stamped version 3, and ``channel`` is gone — from the event *and*
    from the redaction allowlist (an allowlist keeps only what is named)."""
    assert AUDIT_SCHEMA_VERSION == 3  # pinned: v3 = channel→guard (audit.py docstring)
    event = build_event(
        guard="git",
        correlation_id="cid",
        method="push",
        project="group/proj",
        decision=Decision(True, "R2", "ok"),
        state=StateView(),
        started=0.0,
        upstream_status=200,
    )
    assert event["schema"] == 3
    assert event["guard"] == "git"
    assert "channel" not in event
    assert "guard" in _ALLOWED_FIELDS and "channel" not in _ALLOWED_FIELDS


def test_redact_keeps_only_allowlisted_fields():
    entry = {
        "ts": 1.0,
        "rule": "R2",
        "reason": "ok",
        "authorization": "Bearer secret",  # must be dropped
        "private-token": "tok",  # must be dropped
        "body": "payload",  # unknown field, dropped
    }
    assert redact(entry) == {"ts": 1.0, "rule": "R2", "reason": "ok"}


async def test_log_writes_one_redacted_json_line(tmp_path):
    path = tmp_path / "audit.jsonl"
    al = AuditLog(str(path))
    al.start()
    al.log({"guard": "api", "rule": "R3", "decision": "allow", "authorization": "secret"})
    await al.stop()  # drains the queue before returning

    lines = path.read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["guard"] == "api" and rec["rule"] == "R3" and rec["decision"] == "allow"
    assert "ts" in rec  # timestamp stamped by log()
    assert "authorization" not in rec  # the secret never reached disk


async def test_log_appends_across_calls(tmp_path):
    path = tmp_path / "audit.jsonl"
    al = AuditLog(str(path))
    al.start()
    al.log({"rule": "R1", "decision": "allow"})
    al.log({"rule": "R4", "decision": "deny"})
    await al.stop()

    rules = [json.loads(line)["rule"] for line in path.read_text().splitlines()]
    assert rules == ["R1", "R4"]  # O_APPEND, in order


async def test_log_to_dash_goes_to_stderr_not_a_file(capsys):
    al = AuditLog("-")
    al.start()
    al.log({"rule": "R1", "decision": "allow"})
    await al.stop()
    assert '"rule":"R1"' in capsys.readouterr().err


async def test_write_failure_is_swallowed_not_fatal(tmp_path, capsys, monkeypatch):
    # Fail-safe (§6.8): a write error must not kill the drain task or block policy.
    al = AuditLog(str(tmp_path / "a.jsonl"))

    def boom(_line: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(al, "_write", boom)
    al.start()
    al.log({"rule": "R1", "decision": "allow"})
    await al.stop()  # would hang/raise if the failure crashed the task
    assert "audit write failed" in capsys.readouterr().err


# --- build_event (F6): one record shape, shared by api_proxy and git_proxy ----


def test_build_event_api_guard_has_exactly_the_expected_fields():
    event = build_event(
        guard="api",
        correlation_id="cid-1",
        method="POST",
        project="group/proj",
        decision=Decision(True, "R3", "ok"),
        state=StateView(open_mrs=1, open_branches=2, writes_last_hour=3),
        started=0.0,
        upstream_status=201,
        path="/projects/1/merge_requests",
        kind="mr",
    )
    assert set(event) == {
        "schema",
        "guard",
        "correlation_id",
        "method",
        "path",
        "project",
        "decision",
        "rule",
        "reason",
        "kind",
        "upstream_status",
        "latency_ms",
        "open_mrs",
        "open_branches",
        "writes_last_hour",
    }
    assert set(event) <= _ALLOWED_FIELDS | {"ts"}  # every field survives redact()
    assert event["schema"] == AUDIT_SCHEMA_VERSION
    assert event["guard"] == "api"
    assert event["decision"] == "allow"
    assert event["rule"] == "R3"
    assert event["path"] == "/projects/1/merge_requests"
    assert event["kind"] == "mr"


def test_build_event_git_guard_has_exactly_the_expected_fields():
    event = build_event(
        guard="git",
        correlation_id="cid-2",
        method="push",
        project="group/proj",
        decision=Decision(False, "R2", "no"),
        state=StateView(),
        started=0.0,
        upstream_status=None,
        refs=["aaaaaaaa→bbbbbbbb refs/heads/claude/x"],
    )
    assert set(event) == {
        "schema",
        "guard",
        "correlation_id",
        "method",
        "project",
        "decision",
        "rule",
        "reason",
        "refs",
        "upstream_status",
        "latency_ms",
        "open_mrs",
        "open_branches",
        "writes_last_hour",
    }
    assert set(event) <= _ALLOWED_FIELDS | {"ts"}  # every field survives redact()
    assert event["schema"] == AUDIT_SCHEMA_VERSION
    assert event["guard"] == "git"
    assert event["decision"] == "deny"
    assert event["rule"] == "R2"
    assert event["refs"] == ["aaaaaaaa→bbbbbbbb refs/heads/claude/x"]
