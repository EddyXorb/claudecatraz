"""What init writes must load through the warden unchanged: the generated
warden.toml + grouped token files parse, and access_mode(host) reflects exactly
which tokens the wizard stored. Skipped when the warden source tree is absent."""

import argparse
import importlib
import shutil
import sys
import types
from pathlib import Path

import pytest

from catraz.commands import setup
from catraz.paths import asset_root
from catraz.ui import Out

_WARDEN_DIR = Path(__file__).resolve().parents[2] / "warden"


def _load_warden_config(root: Path, host: str) -> str:
    """Resolve the warden's access_mode for *host* from what init just wrote."""
    if not _WARDEN_DIR.is_dir():
        pytest.skip("warden source tree not present")
    if str(_WARDEN_DIR) not in sys.path:
        sys.path.insert(0, str(_WARDEN_DIR))
    config_load = importlib.import_module("warden.core.config_load")
    secrets = root / ".catraz" / "secrets"
    env = {
        "READ_TOKENS_FILE": str(secrets / "read_tokens"),
        "WRITE_TOKENS_FILE": str(secrets / "write_tokens"),
    }
    cfg = config_load.from_env(
        env, strict=True, toml_path=str(root / ".catraz" / "config" / "warden.toml")
    )
    return str(cfg.access_mode(host))


def _make_root(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    cat = root / ".catraz"
    cat.mkdir()
    (cat / "config").mkdir()
    shutil.copy2(asset_root() / "assets" / "config" / "warden.toml", cat / "config" / "warden.toml")
    (cat / ".env").write_text("DEV_UID=1000\nAUTH_MODE=subscription\n")
    return root


def _patch_common(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("catraz.commands.setup._run_sync", lambda *a, **kw: None)
    monkeypatch.setattr(
        "catraz.commands.setup.run_doctor",
        lambda *a, **kw: types.SimpleNamespace(items=[]),
    )
    monkeypatch.setattr("catraz.commands.setup.print_findings", lambda *a, **kw: (0, 0))


def _yes_args() -> argparse.Namespace:
    return argparse.Namespace(
        yes=True, force=False, skip_sync=True, dir=None, no_color=True, print_only=False
    )


def test_read_write_setup_loads_read_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_root(tmp_path)
    _patch_common(monkeypatch)
    monkeypatch.setenv("GITLAB_HOST", "gitlab.example.com")
    monkeypatch.setenv("GITLAB_READ_TOKEN", "glpat-read")
    monkeypatch.setenv("GITLAB_WRITE_TOKEN", "glpat-write")
    setup.cmd_init(root, _yes_args(), Out(color=False))
    assert _load_warden_config(root, "gitlab.example.com") == "read-write"


def test_read_only_setup_loads_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_root(tmp_path)
    _patch_common(monkeypatch)
    monkeypatch.setenv("GITLAB_READ_TOKEN", "glpat-read")  # default host gitlab.com
    setup.cmd_init(root, _yes_args(), Out(color=False))
    assert _load_warden_config(root, "gitlab.com") == "read-only"


def test_no_token_setup_loads_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _make_root(tmp_path)
    _patch_common(monkeypatch)
    setup.cmd_init(root, _yes_args(), Out(color=False))
    assert _load_warden_config(root, "gitlab.com") == "closed"
