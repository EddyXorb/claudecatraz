"""Tests for P8: catraz reload — recreate infra services with stale config."""
import datetime
import os
import types

import pytest

from catraz import compose
from catraz.commands import reload as reload_cmd
from catraz.ui import Out
from catraz.errors import EXIT_OK

UTC = datetime.timezone.utc


def _out():
    return Out(color=False)


def _seed(tmp_path):
    cat = tmp_path / ".catraz"
    (cat / "config").mkdir(parents=True, exist_ok=True)
    (cat / ".env").write_text("AUTH_MODE=api_key\n")
    (cat / "config" / "warden.toml").write_text("# warden\n")
    (cat / "config" / "squid.conf").write_text("# squid\n")
    (cat / "config" / "allowlist.txt").write_text("example.com\n")


# ── _parse_docker_time ────────────────────────────────────────────────────────

def test_parse_docker_time_nanos():
    dt = compose._parse_docker_time("2026-06-28T10:11:12.123456789Z")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.microsecond == 123456  # nanos truncated to micros


def test_parse_docker_time_micros():
    dt = compose._parse_docker_time("2026-06-28T10:11:12.123456Z")
    assert dt is not None and dt.microsecond == 123456


def test_parse_docker_time_no_fraction():
    dt = compose._parse_docker_time("2026-06-28T10:11:12Z")
    assert dt is not None and dt.microsecond == 0


def test_parse_docker_time_garbage():
    assert compose._parse_docker_time("not-a-time") is None
    assert compose._parse_docker_time("") is None


# ── stale_services ────────────────────────────────────────────────────────────

def test_stale_services_changed_file_listed(tmp_path):
    """Tight window: file stat'd just after start → service listed (catches tz bug)."""
    _seed(tmp_path)
    start = datetime.datetime.now(UTC)
    later = (start + datetime.timedelta(seconds=2)).timestamp()
    os.utime(tmp_path / ".catraz" / "config" / "warden.toml", (later, later))
    stale = reload_cmd.stale_services(tmp_path, {"gitlab-warden": start})
    assert "gitlab-warden" in stale


def test_stale_services_untouched_not_listed(tmp_path):
    """Start time in the future, file untouched → not stale."""
    _seed(tmp_path)
    start = datetime.datetime.now(UTC) + datetime.timedelta(seconds=30)
    stale = reload_cmd.stale_services(tmp_path, {"gitlab-warden": start})
    assert "gitlab-warden" not in stale


def test_stale_services_none_start_listed(tmp_path):
    """A present service with unknown (None) start time → stale, not skipped."""
    _seed(tmp_path)
    stale = reload_cmd.stale_services(tmp_path, {"gitlab-warden": None})
    assert "gitlab-warden" in stale
    assert stale["gitlab-warden"] == ["<unknown start>"]


# ── cmd_reload ────────────────────────────────────────────────────────────────

def test_cmd_reload_up_to_date_no_up_call(monkeypatch, tmp_path):
    """Nothing stale → EXIT_OK and no `up` compose call."""
    _seed(tmp_path)
    rows = [
        {"Service": "gitlab-warden", "Name": "c-warden"},
        {"Service": "forward-proxy", "Name": "c-proxy"},
    ]
    future = datetime.datetime.now(UTC) + datetime.timedelta(hours=1)
    calls = []
    monkeypatch.setattr(reload_cmd.compose, "prepare", lambda *a, **kw: ["docker", "compose"])
    monkeypatch.setattr(reload_cmd, "compose_ps", lambda *a, **kw: rows)
    monkeypatch.setattr(reload_cmd.compose, "container_started_at",
                        lambda root, name, **kw: future)
    monkeypatch.setattr(reload_cmd.compose, "run",
                        lambda root, args, **kw: calls.append(args))
    rc = reload_cmd.cmd_reload(tmp_path, types.SimpleNamespace(print_only=False), _out())
    assert rc == EXIT_OK
    assert not any("up" in c for c in calls)


def test_cmd_reload_stale_force_recreates(monkeypatch, tmp_path):
    """A stale service → `up -d --force-recreate <service>` issued."""
    _seed(tmp_path)
    rows = [{"Service": "gitlab-warden", "Name": "c-warden"}]
    old = datetime.datetime(2000, 1, 1, tzinfo=UTC)
    calls = []
    monkeypatch.setattr(reload_cmd.compose, "prepare", lambda *a, **kw: ["docker", "compose"])
    monkeypatch.setattr(reload_cmd, "compose_ps", lambda *a, **kw: rows)
    monkeypatch.setattr(reload_cmd.compose, "container_started_at",
                        lambda root, name, **kw: old)
    monkeypatch.setattr(reload_cmd.compose, "run",
                        lambda root, args, **kw: calls.append(args)
                        or types.SimpleNamespace(returncode=0))
    rc = reload_cmd.cmd_reload(tmp_path, types.SimpleNamespace(print_only=False), _out())
    assert rc == EXIT_OK
    assert calls == [["up", "-d", "--force-recreate", "gitlab-warden"]]


def test_cmd_reload_not_running(monkeypatch, tmp_path):
    """No running containers → EXIT_OK, no up call."""
    _seed(tmp_path)
    calls = []
    monkeypatch.setattr(reload_cmd.compose, "prepare", lambda *a, **kw: ["docker", "compose"])
    monkeypatch.setattr(reload_cmd, "compose_ps", lambda *a, **kw: [])
    monkeypatch.setattr(reload_cmd.compose, "run",
                        lambda root, args, **kw: calls.append(args))
    rc = reload_cmd.cmd_reload(tmp_path, types.SimpleNamespace(print_only=False), _out())
    assert rc == EXIT_OK
    assert calls == []
