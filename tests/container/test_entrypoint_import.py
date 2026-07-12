import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
from typing import Any


def _load_entrypoint() -> Any:
    p = Path(__file__).resolve().parents[2] / "src/catraz/assets/container/entrypoint.py"
    loader = SourceFileLoader("entrypoint", str(p))
    spec = importlib.util.spec_from_loader("entrypoint", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_entrypoint_imports() -> None:
    mod = _load_entrypoint()
    assert hasattr(mod, "cmd_start") and hasattr(mod, "cmd_sync")


def test_read_branch_prefixes_reads_git_rules(tmp_path: Path) -> None:
    mod = _load_entrypoint()
    toml = tmp_path / "warden.toml"
    toml.write_text('[git.rules]\nbranch_prefixes = ["bot/", "agent/"]\n')
    assert mod._read_branch_prefixes(toml) == ("bot/", "agent/")


def test_read_branch_prefixes_defaults_when_unset(tmp_path: Path) -> None:
    mod = _load_entrypoint()
    toml = tmp_path / "warden.toml"
    toml.write_text('[[git.endpoint]]\nhost = "gitlab.com"\ntype = "gitlab"\n')
    assert mod._read_branch_prefixes(toml) == ("claude/",)


def test_read_branch_prefixes_ignores_top_level_key(tmp_path: Path) -> None:
    """A stale top-level branch_prefixes must not leak into the rendered
    instructions — only [git.rules] is consulted."""
    mod = _load_entrypoint()
    toml = tmp_path / "warden.toml"
    toml.write_text('branch_prefixes = ["stale/"]\n')
    assert mod._read_branch_prefixes(toml) == ("claude/",)
