"""`_sync._credentials_mode` resolves through the shared resolver, so
`catraz sync` / auto-sync gate on the same effective mode as the compose
overlay and the in-container adapter."""

from pathlib import Path

from catraz.commands.setup import _sync as setup_sync


def _env(root: Path, body: str) -> None:
    (root / ".catraz").mkdir(parents=True, exist_ok=True)
    (root / ".catraz/.env").write_text(body)


def test_sync_gating_honors_env_override_to_sync(tmp_path: Path) -> None:
    _env(tmp_path, "CLAUDE_CREDENTIALS_MODE=sync\n")
    assert setup_sync._credentials_mode(tmp_path) == "sync"


def test_sync_gating_honors_env_override_to_persistent(tmp_path: Path) -> None:
    _env(tmp_path, "CLAUDE_CREDENTIALS_MODE=persistent\n")
    assert setup_sync._credentials_mode(tmp_path) == "persistent"


def test_sync_gating_invalid_value_falls_back_to_manifest(tmp_path: Path) -> None:
    _env(tmp_path, "CLAUDE_CREDENTIALS_MODE=bogus\n")
    assert setup_sync._credentials_mode(tmp_path) == "persistent"
