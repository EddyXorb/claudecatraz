from pathlib import Path
from typing import Any
import pytest


def test_sync_materializes_claude_json(ep: Any, tmp_path: Path) -> None:
    src = tmp_path / "src" / ".claude"
    src.mkdir(parents=True)
    (src / ".credentials.json").write_text("{}")  # no host ~/.claude.json
    home = tmp_path / "dst"
    adapter = ep._load_adapter()
    ep.cmd_sync(adapter, home, source=str(src))
    assert (home / ".credentials.json").exists()
    assert (home / ".claude.json").exists()  # synthesized default


def test_sync_missing_source_exits(ep: Any, tmp_path: Path) -> None:
    adapter = ep._load_adapter()
    with pytest.raises(SystemExit):
        ep.cmd_sync(adapter, tmp_path / "dst", source=str(tmp_path / "nowhere"))


def test_sync_generic_entrypoint_fails_closed_without_adapter_support(
    ep: Any,
    tmp_path: Path,
    fake_adapter_cls: Any,
) -> None:
    """§05.6: an adapter with no `sync_from_host` (persistent-only) makes the
    generic entrypoint's `cmd_sync` fail closed with a clear message, rather
    than silently doing nothing or crashing with AttributeError."""
    adapter = fake_adapter_cls()
    with pytest.raises(SystemExit) as ei:
        ep.cmd_sync(adapter, tmp_path / "dst")
    assert "persistent" in str(ei.value).lower()
