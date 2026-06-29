from pathlib import Path
from typing import Any
import pytest


def test_build_home_oneoff_run_no_bypass(ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path/".claude"; home.mkdir()
    monkeypatch.setattr(ep.Path, "home", staticmethod(lambda: tmp_path))
    ep.build_claude_home(home, "api_key", remote=False)
    import json; cj = json.loads((tmp_path/".claude.json").read_text())
    assert "bypassPermissionsModeAccepted" not in cj
