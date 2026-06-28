import types
import pytest
from catraz import paths
from catraz.commands import setup
from catraz.commands.setup import _sync as setup_sync
from catraz.errors import CliError


class _Out:
    """Minimal recorder for out.info/warn/err."""
    def __init__(self): self.msgs = []
    def info(self, s): self.msgs.append(("info", s))
    def warn(self, s): self.msgs.append(("warn", s))
    def err(self, s): self.msgs.append(("err", s))


def _seed_env(tmp_path, mode="subscription"):
    (tmp_path / ".catraz").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".catraz/.env").write_text(f"AUTH_MODE={mode}\n")


def _make_seed_cred(tmp_path):
    ch = paths.claude_home(tmp_path)
    ch.mkdir(parents=True, exist_ok=True)
    (ch / ".credentials.json").write_text("{}")


# ── _auto_sync_if_needed ───────────────────────────────────────────────────────

def test_missing_seed_syncs_loud(tmp_path, monkeypatch):
    _seed_env(tmp_path)
    calls = {}
    monkeypatch.setattr(setup_sync, "_run_sync", lambda root, out, **k: calls.update(k))
    out = _Out()
    setup._auto_sync_if_needed(tmp_path, out)
    assert calls.get("quiet") is False                     # loud sync when the seed is absent
    assert any(t == "info" for t, _ in out.msgs)


def test_present_seed_refreshes_silently(tmp_path, monkeypatch):
    _seed_env(tmp_path); _make_seed_cred(tmp_path)
    calls = {}
    monkeypatch.setattr(setup_sync, "_run_sync", lambda root, out, **k: calls.update(k))
    out = _Out()
    setup._auto_sync_if_needed(tmp_path, out)
    assert calls.get("quiet") is True                      # refresh of an existing seed is quiet
    assert out.msgs == []                                  # … and emits nothing


def test_present_seed_failure_does_not_nag(tmp_path, monkeypatch):
    _seed_env(tmp_path); _make_seed_cred(tmp_path)
    def boom(root, out, **k): raise CliError("host unreachable", 1)
    monkeypatch.setattr(setup_sync, "_run_sync", boom)
    out = _Out()
    setup._auto_sync_if_needed(tmp_path, out)              # must not raise
    assert all(t != "warn" for t, _ in out.msgs)           # existing seed still works → silent


def test_missing_seed_failure_warns(tmp_path, monkeypatch):
    _seed_env(tmp_path)
    def boom(root, out, **k): raise CliError("not authenticated", 1)
    monkeypatch.setattr(setup_sync, "_run_sync", boom)
    out = _Out()
    setup._auto_sync_if_needed(tmp_path, out)
    assert any(t == "warn" for t, _ in out.msgs)           # absent seed + failure is a real problem


def test_api_key_mode_is_noop(tmp_path, monkeypatch):
    _seed_env(tmp_path, mode="api_key")
    called = {"n": 0}
    monkeypatch.setattr(setup_sync, "_run_sync", lambda *a, **k: called.update(n=called["n"] + 1))
    setup._auto_sync_if_needed(tmp_path, _Out())
    assert called["n"] == 0


# ── _run_sync quiet plumbing ───────────────────────────────────────────────────

def test_run_sync_quiet_toggles_capture_output(tmp_path, monkeypatch):
    _seed_env(tmp_path)
    fake = tmp_path / "cache"
    entry = fake / "assets/container/entrypoint.py"
    entry.parent.mkdir(parents=True); entry.write_text("# tool")
    monkeypatch.setattr(paths, "asset_root", lambda: fake)
    seen = {}
    monkeypatch.setattr(setup_sync.subprocess, "run",
                        lambda cmd, **k: seen.update(k) or types.SimpleNamespace(returncode=0))
    setup._run_sync(tmp_path, _Out(), quiet=True)
    assert seen.get("capture_output") is True
    seen.clear()
    setup._run_sync(tmp_path, _Out(), quiet=False)
    assert seen.get("capture_output") is False
