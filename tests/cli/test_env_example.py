from pathlib import Path
import pytest
from catraz import paths


def test_env_example_has_no_dead_mount_vars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    txt = (paths.asset_root() / "assets" / ".env.example").read_text()
    assert "PROJECT_DIR=" not in txt
    assert "CLAUDE_HOME=" not in txt
    assert "AUTH_MODE=" in txt
