import pytest
from pathlib import Path
from typing import cast
from catraz import paths
from catraz.commands import setup
from catraz.commands.setup import _sync as setup_sync
from catraz.errors import CliError
from catraz.ui import Out


class _Out:
    """Minimal recorder for out.info/warn/err."""
    def __init__(self) -> None:
        self.msgs: list[tuple[str, str]] = []
    def info(self, s: str) -> None:
        self.msgs.append(("info", s))
    def warn(self, s: str) -> None:
        self.msgs.append(("warn", s))
    def err(self, s: str) -> None:
        self.msgs.append(("err", s))


def _seed_env(tmp_path: Path, mode: str = "subscription") -> None:
    (tmp_path / ".catraz").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".catraz/.env").write_text(f"AUTH_MODE={mode}\n")


def _make_seed_cred(tmp_path: Path) -> None:
    ch = paths.claude_home(tmp_path)
    ch.mkdir(parents=True, exist_ok=True)
    (ch / ".credentials.json").write_text("{}")


@pytest.fixture(autouse=True)
def _sync_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """§05.6: the claude profile's default is credentials.mode=persistent,
    which makes `_auto_sync_if_needed`/`_run_sync` a no-op / a refusal. These
    tests exercise the `credentials.mode=sync` behaviour (the historical
    host->sandbox seed refresh), so pin the mode explicitly rather than
    relying on the shipped default — persistent-mode behaviour has its own
    tests below (`TestPersistentModeSkipsSync`)."""
    monkeypatch.setattr(setup_sync, "_credentials_mode", lambda root: "sync")


# ── _auto_sync_if_needed ───────────────────────────────────────────────────────

def test_missing_seed_syncs_loud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_env(tmp_path)
    calls: dict[str, object] = {}
    monkeypatch.setattr(setup_sync, "_run_sync", lambda root, out, **k: calls.update(k))
    out = _Out()
    setup._auto_sync_if_needed(tmp_path, cast(Out, out))
    assert calls.get("quiet") is False                     # loud sync when the seed is absent
    assert any(t == "info" for t, _ in out.msgs)


def test_present_seed_refreshes_silently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_env(tmp_path); _make_seed_cred(tmp_path)
    calls: dict[str, object] = {}
    monkeypatch.setattr(setup_sync, "_run_sync", lambda root, out, **k: calls.update(k))
    out = _Out()
    setup._auto_sync_if_needed(tmp_path, cast(Out, out))
    assert calls.get("quiet") is True                      # refresh of an existing seed is quiet
    assert out.msgs == []                                  # … and emits nothing


def test_present_seed_failure_does_not_nag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_env(tmp_path); _make_seed_cred(tmp_path)
    def boom(root: object, out: object, **k: object) -> None:
        raise CliError("host unreachable", 1)
    monkeypatch.setattr(setup_sync, "_run_sync", boom)
    out = _Out()
    setup._auto_sync_if_needed(tmp_path, cast(Out, out))              # must not raise
    assert all(t != "warn" for t, _ in out.msgs)           # existing seed still works → silent


def test_missing_seed_failure_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_env(tmp_path)
    def boom(root: object, out: object, **k: object) -> None:
        raise CliError("not authenticated", 1)
    monkeypatch.setattr(setup_sync, "_run_sync", boom)
    out = _Out()
    setup._auto_sync_if_needed(tmp_path, cast(Out, out))
    assert any(t == "warn" for t, _ in out.msgs)           # absent seed + failure is a real problem


def test_api_key_mode_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_env(tmp_path, mode="api_key")
    called: dict[str, int] = {"n": 0}
    monkeypatch.setattr(setup_sync, "_run_sync", lambda *a, **k: called.update(n=called["n"] + 1))
    setup._auto_sync_if_needed(tmp_path, cast(Out, _Out()))
    assert called["n"] == 0


# ── _run_sync quiet plumbing ───────────────────────────────────────────────────

def test_run_sync_quiet_suppresses_adapter_stdout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    """quiet=True redirects the adapter's own stdout (e.g. `sync_from_host`'s
    "Credentials synced into …" print) away from the terminal."""
    _seed_env(tmp_path)

    class _PrintingAdapter:
        def sync_from_host(self, source: object, home: object) -> None:
            print("Credentials synced into somewhere")

    monkeypatch.setattr(setup_sync, "load_adapter_module", lambda profile: _PrintingAdapter())
    setup._run_sync(tmp_path, cast(Out, _Out()), quiet=True)
    assert "Credentials synced" not in capsys.readouterr().out
    setup._run_sync(tmp_path, cast(Out, _Out()), quiet=False)
    assert "Credentials synced" in capsys.readouterr().out


# ── §05.6: credentials.mode=persistent skips/refuses sync ──────────────────────

class TestPersistentModeSkipsSync:
    """The claude profile's *shipped* default is credentials.mode=persistent
    (§05.6, Maintainer-Entscheid 2026-07) — these run against the real
    manifest, so they must NOT inherit the module's autouse `_sync_mode` pin."""

    @pytest.fixture(autouse=True)
    def _sync_mode(self) -> None:
        """Shadows the module-level autouse `_sync_mode` fixture (same name,
        class scope wins) for this class only — these tests are specifically
        about the unpinned, shipped default, not the sync-mode pin."""
        return None

    def test_auto_sync_is_noop_for_persistent_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _seed_env(tmp_path)  # AUTH_MODE=subscription, no AGENT_PROFILE override -> default "claude"
        called: dict[str, int] = {"n": 0}
        monkeypatch.setattr(setup_sync, "_run_sync", lambda *a, **k: called.update(n=called["n"] + 1))
        setup._auto_sync_if_needed(tmp_path, cast(Out, _Out()))
        assert called["n"] == 0

    def test_explicit_sync_refuses_with_clear_message(self, tmp_path: Path) -> None:
        _seed_env(tmp_path)
        with pytest.raises(CliError) as ei:
            setup._run_sync(tmp_path, cast(Out, _Out()))
        assert "persistent" in str(ei.value).lower()
