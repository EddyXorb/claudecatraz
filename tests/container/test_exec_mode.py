from pathlib import Path
from typing import Any
import pytest


def _stub_bootstrap(ep: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise the side-effecting bootstrap steps so the test only observes exec()."""
    monkeypatch.setattr(ep, "drop_to_dev", lambda: None)
    monkeypatch.setattr(ep, "build_claude_home", lambda *a, **kw: None)
    monkeypatch.setattr(ep, "configure_git_warden", lambda: None)
    monkeypatch.delenv("AUTH_MODE", raising=False)


def test_exec_default_bash(ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, list[str]]] = []
    _stub_bootstrap(ep, monkeypatch)
    monkeypatch.setattr(ep.os, "execvp", lambda prog, argv: calls.append((prog, argv)))
    ep.cmd_exec(tmp_path / ".claude", [])
    assert calls == [("bash", ["bash"])]


def test_exec_passthrough(ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, list[str]]] = []
    _stub_bootstrap(ep, monkeypatch)
    monkeypatch.setattr(ep.os, "execvp", lambda prog, argv: calls.append((prog, argv)))
    ep.cmd_exec(tmp_path / ".claude", ["ls", "-la"])
    assert calls == [("ls", ["ls", "-la"])]


def test_exec_configures_claude_home_and_warden(ep: Any, tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """Shell lands in the same configured state as claude/claude-remote: the
    Claude-home is built and git is routed through the warden before exec."""
    seen: list[str] = []
    monkeypatch.setattr(ep, "drop_to_dev", lambda: None)
    monkeypatch.setattr(ep, "build_claude_home",
                        lambda *a, **kw: seen.append("build_claude_home"))
    monkeypatch.setattr(ep, "configure_git_warden",
                        lambda: seen.append("configure_git_warden"))
    monkeypatch.setattr(ep.os, "execvp", lambda prog, argv: seen.append("execvp"))
    monkeypatch.delenv("AUTH_MODE", raising=False)
    ep.cmd_exec(tmp_path / ".claude", [])
    assert seen == ["build_claude_home", "configure_git_warden", "execvp"]
