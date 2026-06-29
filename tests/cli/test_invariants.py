"""Unit tests for compose.assert_invariants and compose.assert_real_dirs.

No Docker required — compose.run is monkeypatched to return a fake JSON response.
"""
import copy
import json
import types
from pathlib import Path
from typing import Any
import pytest

from catraz import compose
from catraz.errors import CliError


GOOD = {
    "networks": {"agent-net": {"internal": True}},
    "services": {
        "claude-dev-env": {
            "environment": {},
            "security_opt": ["no-new-privileges:true"],
            "volumes": [
                {"type": "tmpfs", "target": "/workspace/.catraz",
                 "tmpfs": {"mode": 448, "size": 1048576}},
            ],
        }
    },
}


def _patch(monkeypatch: pytest.MonkeyPatch, cfg: Any) -> None:
    monkeypatch.setattr(
        compose,
        "run",
        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=json.dumps(cfg)),
    )


def test_invariants_pass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch(monkeypatch, GOOD)
    compose.assert_invariants(tmp_path)   # no raise


@pytest.mark.parametrize("mut", [
    lambda c: c["networks"]["agent-net"].__setitem__("internal", False),
    lambda c: c["services"]["claude-dev-env"]["environment"].__setitem__("GITLAB_WRITE_TOKEN", "x"),
    lambda c: c["services"]["claude-dev-env"].__setitem__("privileged", True),
    lambda c: c["services"]["claude-dev-env"].__setitem__("volumes", []),
    lambda c: c["services"]["claude-dev-env"].__setitem__("security_opt", []),
    # tmpfs mode wrong (0o755 == 493, not 0o700 == 448)
    lambda c: c["services"]["claude-dev-env"]["volumes"][0]["tmpfs"].__setitem__("mode", 493),
    # tmpfs size missing
    lambda c: c["services"]["claude-dev-env"]["volumes"][0]["tmpfs"].pop("size"),
])
def test_invariants_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mut: Any) -> None:
    bad = copy.deepcopy(GOOD)
    mut(bad)
    _patch(monkeypatch, bad)
    with pytest.raises(CliError):
        compose.assert_invariants(tmp_path)


def test_symlink_guard(tmp_path: Path) -> None:
    (tmp_path / ".catraz").mkdir()
    link = tmp_path / "l"
    link.symlink_to(tmp_path)
    with pytest.raises(CliError):
        compose.assert_real_dirs(link)


def test_t7b_host_symlink_on_project_dir(tmp_path: Path) -> None:
    """T7b: if PROJECT_DIR itself is a symlink catraz up must abort (assert_real_dirs)."""
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / ".catraz").mkdir()
    link = tmp_path / "link-to-real"
    link.symlink_to(real_dir)
    with pytest.raises(CliError):
        compose.assert_real_dirs(link)
