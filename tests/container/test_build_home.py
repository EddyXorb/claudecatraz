"""adapter.prepare_home() behind the AgentAdapter contract, exercised via the
real claude adapter staged by the `ep` fixture. credentials.mode = "persistent"
(the claude default) makes the live home the durable store: prepare_home seeds
each file only when absent and merges flags into an existing .claude.json,
never symlinks. "sync" keeps the tmpfs seed-from-.ro path."""

import json
from pathlib import Path
from typing import Any

import pytest


def _secrets(ep: Any, **overrides: Any) -> Any:
    base = dict(
        auth_mode="subscription",
        subscription_ro_dir=None,
        api_key_file=None,
        api_key_env_fallback="",
        remote=True,
    )
    base.update(overrides)
    return ep.Secrets(**base)


def _force_sync_manifest(adapter: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Override the baked persistent manifest so the sync branch runs."""
    monkeypatch.setattr(
        adapter,
        "_manifest",
        lambda: adapter._Manifest({"command": "claude", "credentials": {"mode": "sync"}}),
    )


def test_prepare_home_subscription_sync(
    ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".claude"
    (home / ".ro").mkdir(parents=True)
    (home / ".ro" / ".credentials.json").write_text("{}")
    (home / ".ro" / ".claude.json").write_text('{"organizationUuid":"org"}')
    monkeypatch.setattr(ep.Path, "home", staticmethod(lambda: tmp_path))
    adapter = ep._load_adapter()
    _force_sync_manifest(adapter, monkeypatch)
    adapter.prepare_home(home, _secrets(ep, subscription_ro_dir=home / ".ro"))
    assert (home / ".credentials.json").exists()
    cj = json.loads((tmp_path / ".claude.json").read_text())
    assert cj["organizationUuid"] == "org" and cj["bypassPermissionsModeAccepted"] is True
    assert (home / "settings.json").exists()


def test_prepare_home_api_key_synthesizes(
    ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / ".claude"
    home.mkdir()
    monkeypatch.setattr(ep.Path, "home", staticmethod(lambda: tmp_path))
    adapter = ep._load_adapter()
    adapter.prepare_home(home, _secrets(ep, auth_mode="api_key"))
    assert not (home / ".credentials.json").exists()
    assert (tmp_path / ".claude.json").exists()


def test_prepare_home_persistent_full_bind_no_symlinks(
    ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """credentials.mode=persistent treats the live home as the durable store:
    no symlinks, no .ro seed, no credential written by prepare_home (the
    in-container `claude login` creates it); settings.json is a real file."""
    home = tmp_path / ".claude"
    home.mkdir()
    monkeypatch.setattr(ep.Path, "home", staticmethod(lambda: tmp_path))
    adapter = ep._load_adapter()
    adapter.prepare_home(home, _secrets(ep))
    assert (home / "settings.json").exists()
    assert not (home / "settings.json").is_symlink()
    assert (tmp_path / ".claude.json").exists()
    # prepare_home never fabricates the credential and never symlinks state.
    assert not (home / ".credentials.json").exists()
    assert not (home / "projects").is_symlink()


def test_prepare_home_persistent_merges_and_seeds_only_when_absent(
    ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An existing .claude.json keeps its own keys (flags merged in), and an
    existing settings.json is left untouched."""
    home = tmp_path / ".claude"
    home.mkdir()
    (home / "settings.json").write_text('{"theme":"custom"}')
    (tmp_path / ".claude.json").write_text('{"organizationUuid":"persisted-org"}')
    monkeypatch.setattr(ep.Path, "home", staticmethod(lambda: tmp_path))
    adapter = ep._load_adapter()
    adapter.prepare_home(home, _secrets(ep))
    cj = json.loads((tmp_path / ".claude.json").read_text())
    assert cj["organizationUuid"] == "persisted-org"  # not clobbered
    assert cj["bypassPermissionsModeAccepted"] is True  # flag merged in
    # settings.json is only seeded when absent — the persisted one survives.
    assert json.loads((home / "settings.json").read_text()) == {"theme": "custom"}


def test_prepare_home_persistent_credential_survives_restart(
    ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A credential written into the bound home (by `claude login`) is still
    there after a simulated restart re-runs prepare_home against the same
    state dir."""
    home = tmp_path / ".claude"
    home.mkdir()
    monkeypatch.setattr(ep.Path, "home", staticmethod(lambda: tmp_path))
    adapter = ep._load_adapter()
    adapter.prepare_home(home, _secrets(ep))  # first start
    (home / ".credentials.json").write_text('{"token":"live-login"}')  # claude login

    adapter.prepare_home(home, _secrets(ep))  # restart, same bound home
    assert (home / ".credentials.json").read_text() == '{"token":"live-login"}'
    assert not (home / ".credentials.json").is_symlink()
