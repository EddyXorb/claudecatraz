from pathlib import Path
from catraz import compose


def test_base_cmd_points_at_asset_and_project(tmp_path, monkeypatch):
    (tmp_path / ".catraz").mkdir()
    cmd = compose.base_cmd(tmp_path)
    assert "--project-directory" in cmd and str(tmp_path) in cmd
    assert cmd[cmd.index("-f") + 1].endswith("assets/compose/docker-compose.yml")


def test_base_cmd_includes_override_when_present(tmp_path):
    (tmp_path / ".catraz").mkdir()
    (tmp_path / ".catraz/compose.override.yml").write_text("services: {}\n")
    assert str(tmp_path / ".catraz/compose.override.yml") in compose.base_cmd(tmp_path)
