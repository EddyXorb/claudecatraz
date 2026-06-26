"""audit.py (W11, §6.8): redaction-by-allowlist and the single-writer drain.

The crown-jewel invariant: secrets (tokens, Authorization) are dropped *by
construction* — the log is an allowlist of fields, never a blocklist. If that
ever regresses, a credential could land on disk.
"""

from __future__ import annotations

import json

from warden.audit import AuditLog, redact


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
    al.log({"channel": "api", "rule": "R3", "decision": "allow", "authorization": "secret"})
    await al.stop()  # drains the queue before returning

    lines = path.read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["channel"] == "api" and rec["rule"] == "R3" and rec["decision"] == "allow"
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
