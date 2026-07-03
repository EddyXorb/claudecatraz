import re
from pathlib import Path
import pytest
from catraz import compose


def test_base_cmd_points_at_asset_and_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".catraz").mkdir()
    cmd = compose.base_cmd(tmp_path)
    assert "--project-directory" in cmd and str(tmp_path) in cmd
    assert cmd[cmd.index("-f") + 1].endswith("assets/compose/docker-compose.yml")


def test_base_cmd_sets_unique_project_name(tmp_path: Path) -> None:
    cmd = compose.base_cmd(tmp_path)
    assert "--project-name" in cmd
    assert cmd[cmd.index("--project-name") + 1] == compose.project_name(tmp_path)


def test_project_name_is_valid_stable_and_unique(tmp_path: Path) -> None:
    a = tmp_path / "work" / "api"
    b = tmp_path / "scratch" / "api"  # same basename, different path
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    na, nb = compose.project_name(a), compose.project_name(b)
    # valid Compose project name
    assert re.fullmatch(r"[a-z0-9][a-z0-9_-]*", na)
    # same basename but DIFFERENT names → no cross-sandbox collision
    assert na != nb
    assert na.startswith("catraz-api-")
    # stable for the same path
    assert compose.project_name(a) == na


def test_project_name_handles_exotic_basename(tmp_path: Path) -> None:
    d = tmp_path / "My Project!!"
    d.mkdir()
    assert re.fullmatch(r"[a-z0-9][a-z0-9_-]*", compose.project_name(d))


def test_base_cmd_includes_override_when_present(tmp_path: Path) -> None:
    (tmp_path / ".catraz").mkdir()
    (tmp_path / ".catraz/compose.override.yml").write_text("services: {}\n")
    assert str(tmp_path / ".catraz/compose.override.yml") in compose.base_cmd(tmp_path)
