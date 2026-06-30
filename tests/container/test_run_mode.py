from pathlib import Path
from typing import Any
import pytest


def test_build_home_oneoff_run_bypass_default(ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run mode (remote=False) starts with bypass permissions pre-accepted too.

    Since the "bypass by default in run mode" change the bypass accept is no longer
    remote-only: build_claude_home pre-accepts it for every mode and also sets the
    canonical settings.json key. The only remaining remote-only flag is remoteDialogSeen.
    """
    home = tmp_path/".claude"; home.mkdir()
    monkeypatch.setattr(ep.Path, "home", staticmethod(lambda: tmp_path))
    ep.build_claude_home(home, "api_key", remote=False)
    import json
    cj = json.loads((tmp_path/".claude.json").read_text())
    assert cj["bypassPermissionsModeAccepted"] is True
    assert "remoteDialogSeen" not in cj            # still remote-only
    settings = json.loads((home/"settings.json").read_text())
    assert settings["skipDangerousModePermissionPrompt"] is True
