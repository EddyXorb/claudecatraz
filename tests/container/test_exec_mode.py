from pathlib import Path
from typing import Any
import pytest


def _stub_bootstrap(ep: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise the side-effecting bootstrap steps so the test only observes exec()."""
    monkeypatch.setattr(ep, "drop_to_dev", lambda: None)
    monkeypatch.setattr(ep, "install_host_gitconfig", lambda home: None)
    monkeypatch.setattr(ep, "configure_git_warden", lambda: None)
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.delenv("REQUIRE_AGENT_INSTRUCTIONS", raising=False)


def test_exec_default_bash(
    ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_adapter_cls: Any
) -> None:
    calls: list[tuple[str, list[str]]] = []
    _stub_bootstrap(ep, monkeypatch)
    monkeypatch.setattr(ep.os, "execvp", lambda prog, argv: calls.append((prog, argv)))
    adapter = fake_adapter_cls(instructions_dest=tmp_path / "CLAUDE.md")
    ep.cmd_exec(adapter, tmp_path / ".claude", [])
    assert calls == [("bash", ["bash"])]


def test_exec_passthrough(
    ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_adapter_cls: Any
) -> None:
    calls: list[tuple[str, list[str]]] = []
    _stub_bootstrap(ep, monkeypatch)
    monkeypatch.setattr(ep.os, "execvp", lambda prog, argv: calls.append((prog, argv)))
    adapter = fake_adapter_cls(instructions_dest=tmp_path / "CLAUDE.md")
    ep.cmd_exec(adapter, tmp_path / ".claude", ["ls", "-la"])
    assert calls == [("ls", ["ls", "-la"])]


def test_exec_configures_home_and_warden(
    ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_adapter_cls: Any
) -> None:
    """Shell lands in the same configured state as a one-off/remote run: the
    home is prepared and git is routed through the warden before exec."""
    seen: list[str] = []
    monkeypatch.setattr(ep, "drop_to_dev", lambda: None)
    monkeypatch.setattr(ep, "install_host_gitconfig", lambda home: None)
    monkeypatch.setattr(
        ep, "configure_git_warden", lambda: seen.append("configure_git_warden")
    )
    monkeypatch.setattr(ep.os, "execvp", lambda prog, argv: seen.append("execvp"))
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.delenv("REQUIRE_AGENT_INSTRUCTIONS", raising=False)

    class _Adapter(fake_adapter_cls):  # type: ignore[misc]
        def prepare_home(self, home: Path, secrets: Any) -> None:
            seen.append("prepare_home")
            super().prepare_home(home, secrets)

    adapter = _Adapter(instructions_dest=tmp_path / "CLAUDE.md")
    ep.cmd_exec(adapter, tmp_path / ".claude", [])
    assert seen == ["prepare_home", "configure_git_warden", "execvp"]
