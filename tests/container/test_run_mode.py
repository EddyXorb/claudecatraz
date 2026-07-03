import json
from pathlib import Path
from typing import Any
import pytest


def test_prepare_home_oneoff_run_bypass_default(
    claude_adapter: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run mode (remote=False) starts with bypass permissions pre-accepted too.

    Since the "bypass by default in run mode" change the bypass accept is no longer
    remote-only: prepare_home pre-accepts it for every mode and also sets the
    canonical settings.json key. The only remaining remote-only flag is remoteDialogSeen.
    """
    home = tmp_path / ".claude"; home.mkdir()
    monkeypatch.setattr(claude_adapter.Path, "home", staticmethod(lambda: tmp_path))
    secrets = claude_adapter.Secrets(
        auth_mode="api_key", subscription_ro_dir=None, persistent_state_dir=None,
        api_key_file=None, api_key_env_fallback="", remote=False,
    )
    claude_adapter.prepare_home(home, secrets)
    cj = json.loads((tmp_path / ".claude.json").read_text())
    assert cj["bypassPermissionsModeAccepted"] is True
    assert "remoteDialogSeen" not in cj            # still remote-only
    settings = json.loads((home / "settings.json").read_text())
    assert settings["skipDangerousModePermissionPrompt"] is True


def test_prepare_home_remote_sets_dialog_seen(
    claude_adapter: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / ".claude"; home.mkdir()
    monkeypatch.setattr(claude_adapter.Path, "home", staticmethod(lambda: tmp_path))
    secrets = claude_adapter.Secrets(
        auth_mode="api_key", subscription_ro_dir=None, persistent_state_dir=None,
        api_key_file=None, api_key_env_fallback="", remote=True,
    )
    claude_adapter.prepare_home(home, secrets)
    cj = json.loads((tmp_path / ".claude.json").read_text())
    assert cj["remoteDialogSeen"] is True
