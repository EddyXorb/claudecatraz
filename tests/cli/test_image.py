import types
from pathlib import Path
from catraz import image

def test_tag_is_content_addressed(tmp_path, monkeypatch):
    df = tmp_path / "Dockerfile"; df.write_text("FROM ubuntu:24.04\n")
    seen = {}
    monkeypatch.setattr(image, "_image_exists", lambda t: False)

    def fake_run(cmd, **k):
        seen.setdefault("tag", cmd[cmd.index("-t") + 1])
        return type("R", (), {"returncode": 0})()
    monkeypatch.setattr(image.subprocess, "run", fake_run)
    image._build_base(df)
    assert seen["tag"].startswith("catraz-base:") and len(seen["tag"].split(":")[1]) == 12

def test_resolve_prefers_base_image(tmp_path):
    (tmp_path/".catraz").mkdir(); (tmp_path/".catraz/.env").write_text("BASE_IMAGE=x/y:1\n")
    assert image.resolve_base(tmp_path) == "x/y:1"


def _seed(tmp_path, env):
    (tmp_path/".catraz").mkdir(); (tmp_path/".catraz/.env").write_text(env)


def test_base_context_overrides_build_dir(tmp_path, monkeypatch):
    df = tmp_path/"docker"/"Dockerfile.base"; df.parent.mkdir(parents=True); df.write_text("FROM scratch\n")
    (tmp_path/"ctxroot").mkdir()
    _seed(tmp_path, "BASE_DOCKERFILE=./docker/Dockerfile.base\nBASE_CONTEXT=./ctxroot\n")
    seen = {}
    def fake_run(cmd, **k):
        seen["cmd"] = cmd; return types.SimpleNamespace(returncode=0)
    monkeypatch.setattr(image.subprocess, "run", fake_run)
    monkeypatch.setattr(image, "_image_exists", lambda t: False)
    image.resolve_base(tmp_path)
    assert str((tmp_path/"ctxroot").resolve()) in seen["cmd"]
    assert str(df.resolve()) in seen["cmd"]


def test_base_context_default_is_dockerfile_dir(tmp_path, monkeypatch):
    df = tmp_path/"docker"/"Dockerfile.base"; df.parent.mkdir(parents=True); df.write_text("FROM scratch\n")
    _seed(tmp_path, "BASE_DOCKERFILE=./docker/Dockerfile.base\n")
    seen = {}
    monkeypatch.setattr(image.subprocess, "run", lambda cmd, **k: seen.update(cmd=cmd) or types.SimpleNamespace(returncode=0))
    monkeypatch.setattr(image, "_image_exists", lambda t: False)
    image.resolve_base(tmp_path)
    assert str(df.parent.resolve()) in seen["cmd"]
