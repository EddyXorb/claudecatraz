import subprocess
import types
from pathlib import Path
from typing import Any
import pytest
from catraz import image


def test_tag_is_content_addressed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    df = tmp_path / "Dockerfile"
    df.write_text("FROM ubuntu:24.04\n")
    seen: dict[str, Any] = {}
    monkeypatch.setattr(image, "_image_exists", lambda t: False)

    def fake_run(cmd: list[str], **k: Any) -> Any:
        seen.setdefault("tag", cmd[cmd.index("-t") + 1])
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(subprocess, "run", fake_run)
    image._build_base(df)
    assert seen["tag"].startswith("catraz-base:") and len(seen["tag"].split(":")[1]) == 12


def test_resolve_prefers_base_image(tmp_path: Path) -> None:
    (tmp_path / ".catraz").mkdir()
    (tmp_path / ".catraz/.env").write_text("BASE_IMAGE=x/y:1\n")
    assert image.resolve_base(tmp_path) == "x/y:1"


def test_resolve_default_uses_local_dockerfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default branch of resolve_base builds .catraz/config/image/Dockerfile."""
    df = tmp_path / ".catraz" / "config" / "image" / "Dockerfile"
    df.parent.mkdir(parents=True)
    df.write_text("FROM ubuntu:24.04\n")
    (tmp_path / ".catraz" / ".env").write_text("")
    seen: dict[str, Any] = {}
    monkeypatch.setattr(image, "_image_exists", lambda t: False)

    def _mock_run_update(cmd: object, **k: Any) -> types.SimpleNamespace:
        seen.update(cmd=cmd)
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", _mock_run_update)
    image.resolve_base(tmp_path)
    assert str(df) in seen["cmd"]


def test_resolve_default_raises_if_dockerfile_missing(tmp_path: Path) -> None:
    """Missing local Dockerfile raises CliError, not FileNotFoundError."""
    (tmp_path / ".catraz").mkdir()
    (tmp_path / ".catraz" / ".env").write_text("")
    with pytest.raises(Exception) as exc_info:
        image.resolve_base(tmp_path)
    assert "catraz init" in str(exc_info.value) or "Dockerfile" in str(exc_info.value)


def _seed(tmp_path: Path, env: str) -> None:
    (tmp_path / ".catraz").mkdir()
    (tmp_path / ".catraz/.env").write_text(env)


def test_base_context_overrides_build_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    df = tmp_path / "docker" / "Dockerfile.base"
    df.parent.mkdir(parents=True)
    df.write_text("FROM scratch\n")
    (tmp_path / "ctxroot").mkdir()
    _seed(tmp_path, "BASE_DOCKERFILE=./docker/Dockerfile.base\nBASE_CONTEXT=./ctxroot\n")
    seen: dict[str, Any] = {}

    def fake_run(cmd: list[str], **k: Any) -> types.SimpleNamespace:
        seen["cmd"] = cmd
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(image, "_image_exists", lambda t: False)
    image.resolve_base(tmp_path)
    assert str((tmp_path / "ctxroot").resolve()) in seen["cmd"]
    assert str(df.resolve()) in seen["cmd"]


def test_base_context_default_is_dockerfile_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    df = tmp_path / "docker" / "Dockerfile.base"
    df.parent.mkdir(parents=True)
    df.write_text("FROM scratch\n")
    _seed(tmp_path, "BASE_DOCKERFILE=./docker/Dockerfile.base\n")
    seen: dict[str, Any] = {}

    def _mock_run_ctx(cmd: object, **k: Any) -> types.SimpleNamespace:
        seen.update(cmd=cmd)
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", _mock_run_ctx)
    monkeypatch.setattr(image, "_image_exists", lambda t: False)
    image.resolve_base(tmp_path)
    assert str(df.parent.resolve()) in seen["cmd"]
