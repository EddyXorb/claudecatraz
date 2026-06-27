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
