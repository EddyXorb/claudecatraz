"""adapter.prepare_home() (§05.2) — the former (Claude-only) build_claude_home,
now behind the AgentAdapter contract. Exercised via the real claude adapter,
staged by the `ep`/`claude_adapter` fixtures (tests/container/conftest.py)."""
import json
from pathlib import Path
from typing import Any
import pytest


def _secrets(ep: Any, **overrides: Any) -> Any:
    base = dict(auth_mode="subscription", subscription_ro_dir=None,
                persistent_state_dir=None, api_key_file=None,
                api_key_env_fallback="", remote=True)
    base.update(overrides)
    return ep.Secrets(**base)


def test_prepare_home_subscription_sync(ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / ".claude"; (home / ".ro").mkdir(parents=True)
    (home / ".ro" / ".credentials.json").write_text("{}")
    (home / ".ro" / ".claude.json").write_text('{"organizationUuid":"org"}')
    monkeypatch.setattr(ep.Path, "home", staticmethod(lambda: tmp_path))
    adapter = ep._load_adapter()
    secrets = _secrets(ep, subscription_ro_dir=home / ".ro")
    adapter.prepare_home(home, secrets)
    assert (home / ".credentials.json").exists()
    cj = json.loads((tmp_path / ".claude.json").read_text())
    assert cj["organizationUuid"] == "org" and cj["bypassPermissionsModeAccepted"] is True
    assert (home / "settings.json").exists()


def test_prepare_home_api_key_synthesizes(ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / ".claude"; home.mkdir()
    monkeypatch.setattr(ep.Path, "home", staticmethod(lambda: tmp_path))
    adapter = ep._load_adapter()
    secrets = _secrets(ep, auth_mode="api_key")
    adapter.prepare_home(home, secrets)
    assert not (home / ".credentials.json").exists()
    assert (tmp_path / ".claude.json").exists()


def test_prepare_home_persistent_wires_selective_symlinks(
    ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§05.6: credentials.mode=persistent (the shipped claude default) wires
    ONLY the credential file + session/project state into the tmpfs home via
    symlinks to the writable per-repo state dir — settings.json stays freshly
    built (A11), never symlinked."""
    home = tmp_path / ".claude"; home.mkdir()
    state_dir = tmp_path / "state"
    monkeypatch.setattr(ep.Path, "home", staticmethod(lambda: tmp_path))
    adapter = ep._load_adapter()
    secrets = _secrets(ep, persistent_state_dir=state_dir)
    adapter.prepare_home(home, secrets)
    assert (home / ".credentials.json").is_symlink()
    assert (home / ".credentials.json").resolve() == (state_dir / ".credentials.json").resolve()
    assert (home / "projects").is_symlink()
    assert (home / "projects").resolve() == (state_dir / "projects").resolve()
    assert state_dir.stat().st_mode & 0o777 == 0o700
    # settings.json is freshly built into the tmpfs home, never persisted:
    assert not (home / "settings.json").is_symlink()
    assert (home / "settings.json").exists()


def test_prepare_home_persistent_survives_missing_target(
    ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First-ever persistent login: the state dir has no `.credentials.json`
    yet — prepare_home must not crash (a dangling symlink is fine; `claude
    login` creates the target through it)."""
    home = tmp_path / ".claude"; home.mkdir()
    state_dir = tmp_path / "state"
    monkeypatch.setattr(ep.Path, "home", staticmethod(lambda: tmp_path))
    adapter = ep._load_adapter()
    secrets = _secrets(ep, persistent_state_dir=state_dir)
    adapter.prepare_home(home, secrets)  # must not raise
    assert (home / ".credentials.json").is_symlink()
    assert not (state_dir / ".credentials.json").exists()  # dangling until first login
