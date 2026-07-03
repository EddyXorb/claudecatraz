"""B2: cmd_run always resolves BASE_IMAGE before calling compose.prepare()."""
import argparse
import typing
import types
from pathlib import Path
import pytest
from catraz import image, compose as compose_mod
from catraz.commands import run as run_cmd
from catraz.ui import Out


def _mock_cmd_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Stub every side-effecting collaborator cmd_run touches."""
    (tmp_path / ".catraz").mkdir()
    (tmp_path / ".catraz" / ".env").write_text("AUTH_MODE=api_key\n")

    prepare_calls: list[dict[str, object]] = []
    compose_calls: list[dict[str, object]] = []

    monkeypatch.setattr(run_cmd, "assert_real_dirs", lambda *a, **k: None)
    monkeypatch.setattr(run_cmd, "assert_invariants", lambda *a, **k: None)
    monkeypatch.setattr(run_cmd, "_ensure_infra", lambda *a, **k: None)
    monkeypatch.setattr(image, "resolve_base", lambda root: "catraz-base:test")
    # Host gitconfig is environment-dependent; pin it off so extra_env is deterministic.
    monkeypatch.setattr(run_cmd, "_host_gitconfig_env", lambda: {})

    def fake_prepare(root: object, *, render: object, extra_env: object = None) -> list[str]:
        prepare_calls.append({"render": render, "extra_env": extra_env})
        return ["docker", "compose", "--project-name", "test"]

    monkeypatch.setattr(compose_mod, "prepare", fake_prepare)

    def fake_compose_run(root: object, args: list[str], *, prefix: object = None, check: bool = True, **k: object) -> types.SimpleNamespace:
        compose_calls.append({"args": list(args), "prefix": prefix})
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(run_cmd, "compose_run", fake_compose_run)
    return prepare_calls, compose_calls


def test_cmd_run_passes_base_image_to_prepare(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """cmd_run passes BASE_IMAGE via extra_env to compose.prepare, not compose_run."""
    prepare_calls, _ = _mock_cmd_run(monkeypatch, tmp_path)
    args = typing.cast(argparse.Namespace, types.SimpleNamespace(claude_args=[]))
    monkeypatch.chdir(tmp_path)
    rc = run_cmd.cmd_run(tmp_path, args, Out(color=False))
    assert rc == 0
    assert len(prepare_calls) == 1
    assert prepare_calls[0]["extra_env"] == {"BASE_IMAGE": "catraz-base:test"}
    assert prepare_calls[0]["render"] is True


def test_host_gitconfig_env_present(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """_host_gitconfig_env exports HOST_GITCONFIG when ~/.gitconfig exists, else nothing."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    assert run_cmd._host_gitconfig_env() == {}            # no ~/.gitconfig yet
    (home / ".gitconfig").write_text("[user]\n\tname = T\n")
    assert run_cmd._host_gitconfig_env() == {"HOST_GITCONFIG": str(home / ".gitconfig")}
