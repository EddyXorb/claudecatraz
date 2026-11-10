"""Agent image lifecycle: foreground only when needed, version bumps in the background."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import types
from pathlib import Path
from typing import Any

import pytest

from catraz import agent_image, cli, image
from catraz.agent_image import Pointer
from catraz.commands import build as build_cmd
from catraz.errors import CliError, EXIT_DOCKER, EXIT_GENERAL, EXIT_OK
from catraz.ui import Out


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project root plus a minimal fake build context."""
    proj = tmp_path / "proj"
    (proj / ".catraz").mkdir(parents=True)
    (proj / ".catraz/.env").write_text("CLAUDE_CODE_VERSION=latest\n")
    ctx = tmp_path / "cache"
    (ctx / "assets/container").mkdir(parents=True)
    (ctx / "assets/container/entrypoint.py").write_text("print('hi')\n")
    (ctx / "assets/agents/claude").mkdir(parents=True)
    (ctx / "assets/agents/claude/layer.Dockerfile").write_text("FROM x\n")
    monkeypatch.setattr(agent_image, "asset_root", lambda: ctx)
    for key in ("NODE_VERSION", "DEV_UID"):
        monkeypatch.delenv(key, raising=False)
    return proj


class Docker:
    """Records docker invocations; `build_ok`/`exists` steer the outcomes."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.build_ok = True
        self.exists = True
        self.listed: list[str] = []
        self.missing = False

    def run(self, cmd: list[str], **kw: Any) -> types.SimpleNamespace:
        if self.missing:
            raise FileNotFoundError("docker")
        self.calls.append(list(cmd))
        if cmd[:2] == ["docker", "build"]:
            out = kw.get("stdout")
            if out is not None and hasattr(out, "write"):
                out.write("step 1/9\n")
            return types.SimpleNamespace(returncode=0 if self.build_ok else 1, stdout="")
        if cmd[:3] == ["docker", "image", "inspect"]:
            return types.SimpleNamespace(returncode=0 if self.exists else 1, stdout="")
        if cmd[:3] == ["docker", "image", "ls"]:
            return types.SimpleNamespace(returncode=0, stdout="\n".join(self.listed))
        return types.SimpleNamespace(returncode=0, stdout="")

    def builds(self) -> list[list[str]]:
        return [c for c in self.calls if c[:2] == ["docker", "build"]]


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch) -> Docker:
    d = Docker()
    monkeypatch.setattr(subprocess, "run", d.run)
    return d


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    calls: list[Path] = []
    monkeypatch.setattr(agent_image, "spawn_refresh", lambda root: calls.append(root))
    return calls


def _registry(monkeypatch: pytest.MonkeyPatch, version: str | None) -> None:
    monkeypatch.setattr(image, "published_claude_code_version", lambda: version)


def _recipe(root: Path, base: str = "base:1") -> str:
    return agent_image.spec_for(root, base).recipe()


def _seed_pointer(root: Path, version: str = "1.0.0", **kw: Any) -> Pointer:
    recipe = kw.pop("recipe", None) or _recipe(root)
    ptr = Pointer(agent_image.tag_for(root, recipe, version), recipe, version, **kw)
    agent_image._write_pointer(root, ptr)
    return ptr


def _out() -> Out:
    return Out(color=False)


# ── recipe / tag / settings ───────────────────────────────────────────────────


def test_recipe_is_stable_and_tracks_every_input(root: Path) -> None:
    ctx = agent_image.spec_for(root, "b").context
    first = _recipe(root)
    assert first == _recipe(root) and len(first) == 12
    assert _recipe(root, "base:2") != first
    (ctx / "container/entrypoint.py").write_text("print('changed')\n")
    second = _recipe(root)
    assert second != first
    (ctx / "agents/claude/layer.Dockerfile").write_text("FROM y\n")
    assert _recipe(root) != second


def test_recipe_ignores_bytecode(root: Path) -> None:
    ctx = agent_image.spec_for(root, "b").context
    before = _recipe(root)
    (ctx / "container/__pycache__").mkdir()
    (ctx / "container/__pycache__/entrypoint.cpython-311.pyc").write_bytes(b"\0")
    (ctx / "container/stray.pyc").write_bytes(b"\0")
    assert _recipe(root) == before


def test_spec_settings_follow_compose_precedence(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (root / ".catraz/.env").write_text("NODE_VERSION=20\nDEV_UID=1234\n")
    spec = agent_image.spec_for(root, "b")
    assert (spec.profile, spec.node, spec.uid) == ("claude", "20", "1234")
    monkeypatch.setenv("NODE_VERSION", "24")
    assert agent_image.spec_for(root, "b").node == "24"
    (root / ".catraz/.env").write_text("")
    monkeypatch.delenv("NODE_VERSION")
    spec = agent_image.spec_for(root, "b")
    assert (spec.node, spec.uid) == ("22", "1000")


def test_recipe_changes_with_node_and_uid(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = _recipe(root)
    monkeypatch.setenv("NODE_VERSION", "24")
    second = _recipe(root)
    monkeypatch.setenv("DEV_UID", "4242")
    assert len({first, second, _recipe(root)}) == 3


def test_tag_is_per_project_and_sanitized(root: Path) -> None:
    tag = agent_image.tag_for(root, "abc123", "2.0.1+build/x")
    repo, _, ver = tag.partition(":")
    assert repo.endswith("-agent") and repo.startswith("catraz-proj-")
    assert ver == "abc123-2.0.1_build_x"


# ── pointer / lock ────────────────────────────────────────────────────────────


def test_pointer_roundtrip_and_fail_closed(root: Path) -> None:
    assert agent_image.read_pointer(root) is None
    ptr = _seed_pointer(root, new=True)
    assert agent_image.read_pointer(root) == ptr
    path = root / ".catraz/image/agent.json"
    for bad in ("{not json", "[]", json.dumps({"tag": "t", "recipe": "", "version": "v"})):
        path.write_text(bad)
        assert agent_image.read_pointer(root) is None
    assert not list(path.parent.glob("*.tmp"))


def test_lock_is_exclusive_and_released(root: Path) -> None:
    assert agent_image.lock_info(root) is None
    assert agent_image._acquire_lock(root)
    assert agent_image.lock_info(root) is not None
    assert not agent_image._acquire_lock(root)
    agent_image._release_lock(root)
    assert agent_image._acquire_lock(root)


def test_stale_lock_is_broken(root: Path) -> None:
    assert agent_image._acquire_lock(root)
    lock = root / ".catraz/image/build.lock"
    old = time.time() - agent_image.LOCK_STALE_SECONDS - 10
    os.utime(lock, (old, old))
    assert agent_image.lock_info(root) is None
    assert agent_image._acquire_lock(root)


def test_unreadable_lock_body_still_counts_as_live(root: Path) -> None:
    lock = root / ".catraz/image/build.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("garbage")
    assert agent_image.lock_info(root) == {}


# ── docker plumbing ───────────────────────────────────────────────────────────


def test_docker_build_args_terminal_mode(root: Path, docker: Docker) -> None:
    spec = agent_image.spec_for(root, "base:1")
    assert agent_image._docker_build(spec, "r", "2.0.0", "repo:t", log=None, no_cache=True)
    cmd = docker.builds()[0]
    assert cmd[cmd.index("-t") + 1] == "repo:t"
    assert cmd[cmd.index("-f") + 1] == str(spec.dockerfile)
    for arg in (
        "BASE_IMAGE=base:1",
        "CLAUDE_CODE_VERSION=2.0.0",
        "NODE_VERSION=22",
        "DEV_UID=1000",
    ):
        assert arg in cmd
    assert "catraz.recipe=r" in cmd and "--no-cache" in cmd
    assert "--progress=plain" not in cmd and cmd[-1] == str(spec.context)


def test_docker_build_log_mode_writes_file(root: Path, docker: Docker, tmp_path: Path) -> None:
    spec = agent_image.spec_for(root, "base:1")
    log = tmp_path / "logs/build.log"
    docker.build_ok = False
    assert not agent_image._docker_build(spec, "r", "2.0.0", "repo:t", log=log)
    assert "--progress=plain" in docker.builds()[0]
    assert "step 1/9" in log.read_text()


def test_docker_missing_is_a_cli_error(root: Path, docker: Docker) -> None:
    docker.missing = True
    spec = agent_image.spec_for(root, "base:1")
    with pytest.raises(CliError) as e:
        agent_image._docker_build(spec, "r", "v", "t", log=None)
    assert e.value.code == EXIT_DOCKER
    with pytest.raises(CliError):
        agent_image._image_exists("t")
    agent_image._prune(root, keep="t")  # best effort, swallowed


def test_prune_removes_every_other_tag(root: Path, docker: Docker) -> None:
    repo = agent_image.repository(root)
    docker.listed = [f"{repo}:a-1", f"{repo}:a-2", f"{repo}:a-3"]
    agent_image._prune(root, keep=f"{repo}:a-3")
    removed = [c[-1] for c in docker.calls if c[:3] == ["docker", "image", "rm"]]
    assert removed == [f"{repo}:a-1", f"{repo}:a-2"]


# ── foreground build / ensure ─────────────────────────────────────────────────


def test_build_switches_pointer_and_clears_failure(
    root: Path, docker: Docker, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _registry(monkeypatch, "3.0.0")
    agent_image._write_json(root / ".catraz/image/last-failure.json", {"version": "x"})
    ptr = agent_image.build(root, _out(), "base:1", reason="testing")
    assert ptr == Pointer(agent_image.tag_for(root, _recipe(root), "3.0.0"), _recipe(root), "3.0.0")
    assert agent_image.read_pointer(root) == ptr
    assert not (root / ".catraz/image/last-failure.json").exists()
    assert "claude-code 3.0.0" in capsys.readouterr().out


def test_build_failure_keeps_pointer(
    root: Path, docker: Docker, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registry(monkeypatch, "3.0.0")
    old = _seed_pointer(root)
    docker.build_ok = False
    with pytest.raises(CliError) as e:
        agent_image.build(root, _out(), "base:1", reason="testing")
    assert e.value.code == EXIT_DOCKER
    assert agent_image.read_pointer(root) == old


def test_ensure_first_run_builds_in_foreground(
    root: Path,
    docker: Docker,
    spawned: list[Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _registry(monkeypatch, "3.0.0")
    tag = agent_image.ensure(root, _out(), "base:1")
    assert tag.endswith("-3.0.0") and len(docker.builds()) == 1
    assert "no agent image built yet" in capsys.readouterr().out
    assert spawned == []


def test_ensure_offline_first_run_builds_latest(
    root: Path, docker: Docker, spawned: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _registry(monkeypatch, None)
    assert agent_image.ensure(root, _out(), "base:1").endswith("-latest")


def test_ensure_reuses_current_image_and_spawns_worker(
    root: Path, docker: Docker, spawned: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_registry() -> str:
        raise AssertionError("the start path must not query the registry")

    monkeypatch.setattr(image, "published_claude_code_version", _no_registry)
    ptr = _seed_pointer(root)
    assert agent_image.ensure(root, _out(), "base:1") == ptr.tag
    assert docker.builds() == [] and spawned == [root]


@pytest.mark.parametrize(
    "case, reason",
    [
        ("recipe", "catraz or its image settings changed"),
        ("pinned", "pinned to 9.9.9"),
        ("missing", "the agent image is missing"),
    ],
)
def test_ensure_foreground_rebuild_reasons(
    root: Path,
    docker: Docker,
    spawned: list[Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    case: str,
    reason: str,
) -> None:
    _registry(monkeypatch, "3.0.0")
    if case == "recipe":
        _seed_pointer(root, recipe="000000000000")
    elif case == "pinned":
        _seed_pointer(root)
        (root / ".catraz/.env").write_text("CLAUDE_CODE_VERSION=9.9.9\n")
    else:
        _seed_pointer(root)
        docker.exists = False
    tag = agent_image.ensure(root, _out(), "base:1")
    assert len(docker.builds()) == 1 and reason in capsys.readouterr().out
    assert agent_image.read_pointer(root) is not None
    assert tag == agent_image.read_pointer(root).tag  # type: ignore[union-attr]
    assert spawned == []


def test_ensure_pinned_match_never_spawns(root: Path, docker: Docker, spawned: list[Path]) -> None:
    (root / ".catraz/.env").write_text("CLAUDE_CODE_VERSION=1.0.0\n")
    ptr = _seed_pointer(root, "1.0.0")
    assert agent_image.ensure(root, _out(), "base:1") == ptr.tag
    assert spawned == [] and docker.builds() == []


def test_ensure_reports_running_background_build(
    root: Path, docker: Docker, spawned: list[Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_pointer(root)
    agent_image._write_json(root / ".catraz/image/build.lock", {"version": "2.0.0"})
    agent_image.ensure(root, _out(), "base:1")
    assert "claude-code 2.0.0 is building in the background" in capsys.readouterr().out
    assert spawned == []


def test_ensure_reports_failed_background_build(
    root: Path, docker: Docker, spawned: list[Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_pointer(root)
    agent_image._write_json(root / ".catraz/image/last-failure.json", {"version": "2.0.0"})
    agent_image.ensure(root, _out(), "base:1")
    err = capsys.readouterr().err
    assert "claude-code 2.0.0 failed" in err and "still on 1.0.0" in err
    assert "image-build.log" in err and spawned == [root]


def test_ensure_announces_switch_once(
    root: Path, docker: Docker, spawned: list[Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _seed_pointer(root, "2.0.0", new=True)
    agent_image.ensure(root, _out(), "base:1")
    assert "updated to claude-code 2.0.0" in capsys.readouterr().out
    agent_image.ensure(root, _out(), "base:1")
    assert "updated" not in capsys.readouterr().out


# ── detached worker ───────────────────────────────────────────────────────────


def test_spawn_refresh_is_detached(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_popen(cmd: list[str], **kw: Any) -> None:
        seen.update(cmd=cmd, **kw)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    agent_image.spawn_refresh(root)
    cmd = seen["cmd"]
    assert cmd[1:] == ["-m", "catraz.cli", "build", "--background", "-C", str(root)]
    assert seen["stdin"] is subprocess.DEVNULL and seen["stderr"] is subprocess.STDOUT
    if os.name == "nt":
        assert seen["creationflags"]
    else:
        assert seen["start_new_session"] is True
    pkg_parent = str(Path(agent_image.__file__).resolve().parents[1])
    assert seen["env"]["PYTHONPATH"].split(os.pathsep)[0] == pkg_parent
    assert (root / ".catraz/logs/image-refresh.log").exists()


def test_spawn_refresh_swallows_os_errors(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **k: Any) -> None:
        raise OSError("no fork")

    monkeypatch.setattr(subprocess, "Popen", boom)
    agent_image.spawn_refresh(root)


@pytest.fixture
def worker(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(image, "resolve_base", lambda r: "base:1")
    return root


def test_refresh_builds_new_version_and_switches(
    worker: Path, docker: Docker, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registry(monkeypatch, "2.0.0")
    old = _seed_pointer(worker, "1.0.0")
    docker.listed = [old.tag]
    assert agent_image.refresh(worker) == EXIT_OK
    ptr = agent_image.read_pointer(worker)
    assert ptr is not None and ptr.version == "2.0.0" and ptr.new
    assert "--progress=plain" in docker.builds()[0]
    assert ["docker", "image", "rm", old.tag] in docker.calls
    assert agent_image.lock_info(worker) is None
    assert (worker / ".catraz/logs/image-build.log").exists()


def test_refresh_failure_keeps_old_image_and_records_it(
    worker: Path, docker: Docker, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registry(monkeypatch, "2.0.0")
    old = _seed_pointer(worker, "1.0.0")
    docker.build_ok = False
    assert agent_image.refresh(worker) == EXIT_GENERAL
    assert agent_image.read_pointer(worker) == old
    fail = json.loads((worker / ".catraz/image/last-failure.json").read_text())
    assert fail["version"] == "2.0.0"
    assert agent_image.lock_info(worker) is None


def test_refresh_same_version_is_a_noop_and_clears_failure(
    worker: Path, docker: Docker, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registry(monkeypatch, "1.0.0")
    _seed_pointer(worker, "1.0.0")
    agent_image._write_json(worker / ".catraz/image/last-failure.json", {"version": "0.9"})
    assert agent_image.refresh(worker) == EXIT_OK
    assert docker.builds() == []
    assert not (worker / ".catraz/image/last-failure.json").exists()


@pytest.mark.parametrize("case", ["held", "pinned", "no-pointer", "recipe", "offline"])
def test_refresh_does_nothing_when_it_must_not_build(
    worker: Path, docker: Docker, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    _registry(monkeypatch, None if case == "offline" else "2.0.0")
    if case != "no-pointer":
        _seed_pointer(worker, recipe="000000000000" if case == "recipe" else None)
    if case == "pinned":
        (worker / ".catraz/.env").write_text("CLAUDE_CODE_VERSION=1.0.0\n")
    if case == "held":
        assert agent_image._acquire_lock(worker)
    assert agent_image.refresh(worker) == EXIT_OK
    assert docker.builds() == []
    assert (agent_image.lock_info(worker) is not None) == (case == "held")


def test_refresh_discards_build_when_recipe_moved_on(
    worker: Path, docker: Docker, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registry(monkeypatch, "2.0.0")
    _seed_pointer(worker, "1.0.0")
    newer = Pointer("repo:other", "ffffffffffff", "1.5.0")
    real_build = agent_image._docker_build

    def build_then_upgrade(*a: Any, **k: Any) -> bool:
        ok = real_build(*a, **k)
        agent_image._write_pointer(worker, newer)  # a foreground build won meanwhile
        return ok

    monkeypatch.setattr(agent_image, "_docker_build", build_then_upgrade)
    assert agent_image.refresh(worker) == EXIT_OK
    assert agent_image.read_pointer(worker) == newer


def test_refresh_reports_cli_errors_and_releases_lock(
    worker: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_base(r: Path) -> str:
        raise CliError("base gone", EXIT_DOCKER)

    monkeypatch.setattr(image, "resolve_base", no_base)
    assert agent_image.refresh(worker) == EXIT_DOCKER
    assert "base gone" in capsys.readouterr().out
    assert agent_image.lock_info(worker) is None


# ── explicit rebuild / CLI ────────────────────────────────────────────────────


def test_rebuild_builds_under_the_lock(
    worker: Path, docker: Docker, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registry(monkeypatch, "2.0.0")
    held: list[bool] = []
    real_build = agent_image._docker_build

    def spy(*a: Any, **k: Any) -> bool:
        held.append(agent_image.lock_info(worker) is not None)
        return real_build(*a, **k)

    monkeypatch.setattr(agent_image, "_docker_build", spy)
    assert agent_image.rebuild(worker, _out(), no_cache=True) == EXIT_OK
    assert held == [True] and "--no-cache" in docker.builds()[0]
    assert agent_image.lock_info(worker) is None


def test_rebuild_refuses_while_worker_runs(worker: Path, docker: Docker) -> None:
    assert agent_image._acquire_lock(worker)
    assert agent_image.rebuild(worker, _out()) == EXIT_GENERAL
    assert docker.builds() == []


def test_rebuild_failure_releases_lock(
    worker: Path, docker: Docker, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registry(monkeypatch, "2.0.0")
    docker.build_ok = False
    with pytest.raises(CliError):
        agent_image.rebuild(worker, _out())
    assert agent_image.lock_info(worker) is None


def test_build_command_parses_and_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    args = cli.build_parser().parse_args(["build", "--no-cache"])
    assert (args.command, args.no_cache, args.background) == ("build", True, False)
    calls: list[tuple[str, Any]] = []

    def fake_refresh(r: Path) -> int:
        calls.append(("bg", r))
        return 0

    def fake_rebuild(r: Path, o: Out, no_cache: bool) -> int:
        calls.append(("fg", no_cache))
        return 0

    monkeypatch.setattr(agent_image, "refresh", fake_refresh)
    monkeypatch.setattr(agent_image, "rebuild", fake_rebuild)
    root = Path("/x")
    build_cmd.cmd_build(root, argparse.Namespace(background=True, no_cache=False), _out())
    build_cmd.cmd_build(root, args, _out())
    assert calls == [("bg", root), ("fg", True)]
    assert cli.HANDLERS["build"] is build_cmd.cmd_build
