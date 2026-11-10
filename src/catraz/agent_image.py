"""Agent image lifecycle. A start runs the last good build at once; a Claude Code
version bump is built by a detached worker and picked up by the next start. Anything
else that shapes the image (the recipe) is built in the foreground, because the image
carries the container half of the CLI's contract and must never lag behind it."""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from catraz import image
from catraz.agents import resolve_agent_profile
from catraz.compose import project_name
from catraz.envfile import load_env
from catraz.errors import CliError, EXIT_DOCKER, EXIT_GENERAL, EXIT_OK
from catraz.paths import asset_root
from catraz.ui import Out

LOCK_STALE_SECONDS = 2 * 60 * 60


@dataclass(frozen=True)
class Spec:
    """Everything except the Claude Code version that determines the agent image."""

    profile: str
    base: str
    node: str
    uid: str
    context: Path

    def recipe(self) -> str:
        h = hashlib.sha256()
        for part in (self.profile, self.base, self.node, self.uid):
            h.update(part.encode() + b"\0")
        for d in (self.context / "container", self.context / "agents" / self.profile):
            files = sorted(
                p
                for p in d.rglob("*")
                if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
            )
            for f in files:
                h.update(f.relative_to(self.context).as_posix().encode() + b"\0")
                h.update(f.read_bytes() + b"\0")
        return h.hexdigest()[:12]

    @property
    def dockerfile(self) -> Path:
        return self.context / "agents" / self.profile / "layer.Dockerfile"


@dataclass(frozen=True)
class Pointer:
    """The last successful build; `new` marks a background switch not yet announced."""

    tag: str
    recipe: str
    version: str
    new: bool = False


def _setting(root: Path, key: str, default: str) -> str:
    # Same precedence compose interpolation applies: shell env over the env-file.
    return os.environ.get(key) or load_env(root / ".catraz/.env").get(key) or default


def spec_for(root: Path, base: str) -> Spec:
    return Spec(
        profile=resolve_agent_profile(root),
        base=base,
        node=_setting(root, "NODE_VERSION", "22"),
        uid=_setting(root, "DEV_UID", "1000"),
        context=asset_root() / "assets",
    )


def repository(root: Path) -> str:
    return f"{project_name(root)}-agent"


def tag_for(root: Path, recipe: str, version: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", version)[:100]
    return f"{repository(root)}:{recipe}-{safe}"


def _state_dir(root: Path) -> Path:
    return root / ".catraz" / "image"


def _pointer_path(root: Path) -> Path:
    return _state_dir(root) / "agent.json"


def _lock_path(root: Path) -> Path:
    return _state_dir(root) / "build.lock"


def _failure_path(root: Path) -> Path:
    return _state_dir(root) / "last-failure.json"


def _log_dir(root: Path) -> Path:
    return root / ".catraz" / "logs"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """Atomic: readers see the old or the new document, never a partial one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def read_pointer(root: Path) -> Pointer | None:
    """None for a missing or malformed pointer, which forces a foreground build."""
    data = _read_json(_pointer_path(root))
    if data is None:
        return None
    fields = (data.get("tag"), data.get("recipe"), data.get("version"))
    if not all(isinstance(v, str) and v for v in fields):
        return None
    return Pointer(str(fields[0]), str(fields[1]), str(fields[2]), data.get("new") is True)


def _write_pointer(root: Path, ptr: Pointer) -> None:
    _write_json(_pointer_path(root), dataclasses.asdict(ptr))


def lock_info(root: Path) -> dict[str, Any] | None:
    """Body of a live build lock, or None when absent or stale."""
    p = _lock_path(root)
    try:
        age = time.time() - p.stat().st_mtime
    except OSError:
        return None
    if age > LOCK_STALE_SECONDS:
        return None
    return _read_json(p) or {}


def _acquire_lock(root: Path) -> bool:
    """O_EXCL create (portable, no fcntl); a stale lock is broken once."""
    p = _lock_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if lock_info(root) is not None:
                return False
            p.unlink(missing_ok=True)
            continue
        with os.fdopen(fd, "w") as f:
            json.dump({"pid": os.getpid(), "started": time.time()}, f)
        return True
    return False


def _release_lock(root: Path) -> None:
    _lock_path(root).unlink(missing_ok=True)


def _image_exists(tag: str) -> bool:
    try:
        r = subprocess.run(["docker", "image", "inspect", tag], capture_output=True)
    except FileNotFoundError:
        raise CliError("`docker` not found on PATH", EXIT_DOCKER)
    return r.returncode == 0


def _docker_build(
    spec: Spec, recipe: str, version: str, tag: str, *, log: Path | None, no_cache: bool = False
) -> bool:
    """Build one tag; log=None streams progress to the terminal, else into the file."""
    cmd = ["docker", "build", "-t", tag, "-f", str(spec.dockerfile)]
    for key, val in (
        ("BASE_IMAGE", spec.base),
        ("NODE_VERSION", spec.node),
        ("CLAUDE_CODE_VERSION", version),
        ("DEV_UID", spec.uid),
    ):
        cmd += ["--build-arg", f"{key}={val}"]
    cmd += ["--label", f"catraz.recipe={recipe}", "--label", f"catraz.claude-code={version}"]
    if no_cache:
        cmd.append("--no-cache")
    if log is not None:
        cmd.append("--progress=plain")
    cmd.append(str(spec.context))
    try:
        if log is None:
            return subprocess.run(cmd).returncode == 0
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "w") as f:
            r = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT)
        return r.returncode == 0
    except FileNotFoundError:
        raise CliError("`docker` not found on PATH", EXIT_DOCKER)


def _prune(root: Path, keep: str) -> None:
    """Best effort: docker refuses to remove an image a container still uses."""
    try:
        r = subprocess.run(
            ["docker", "image", "ls", repository(root), "--format", "{{.Repository}}:{{.Tag}}"],
            capture_output=True,
            text=True,
        )
        for ref in r.stdout.split():
            if ref != keep:
                subprocess.run(["docker", "image", "rm", ref], capture_output=True)
    except FileNotFoundError:
        pass


def build(root: Path, out: Out, base: str, *, reason: str, no_cache: bool = False) -> Pointer:
    """Foreground build with visible progress; switches the pointer on success."""
    spec = spec_for(root, base)
    recipe = spec.recipe()
    version = image.resolve_claude_code_version(root)
    tag = tag_for(root, recipe, version)
    out.info(
        f"• building the agent image (claude-code {version}) — {reason}. "
        "This takes a few minutes; later version updates build in the background."
    )
    if not _docker_build(spec, recipe, version, tag, log=None, no_cache=no_cache):
        raise CliError("agent image build failed — see the build output above", EXIT_DOCKER)
    ptr = Pointer(tag, recipe, version)
    _write_pointer(root, ptr)
    _failure_path(root).unlink(missing_ok=True)
    _prune(root, keep=tag)
    return ptr


def _rebuild_reason(cur: Pointer | None, recipe: str, pinned: str | None) -> str | None:
    if cur is None:
        return "no agent image built yet"
    if cur.recipe != recipe:
        return "catraz or its image settings changed"
    if pinned is not None and cur.version != pinned:
        return f"CLAUDE_CODE_VERSION is pinned to {pinned}"
    if not _image_exists(cur.tag):
        return "the agent image is missing"
    return None


def _report(root: Path, out: Out, cur: Pointer) -> bool:
    """Print the one-line background status; True when a build is running."""
    if cur.new:
        out.info(f"• agent image updated to claude-code {cur.version}")
        _write_pointer(root, dataclasses.replace(cur, new=False))
    lock = lock_info(root)
    if lock is not None:
        version = lock.get("version")
        if isinstance(version, str) and version:
            out.info(
                f"• agent image: claude-code {version} is building in the background "
                "— used on the next start"
            )
        return True
    fail = _read_json(_failure_path(root))
    if fail is not None:
        out.warn(
            f"agent image: background build of claude-code {fail.get('version', '?')} failed "
            f"— still on {cur.version} (log: {_log_dir(root) / 'image-build.log'}; "
            "retry: catraz build)"
        )
    return False


def ensure(root: Path, out: Out, base: str) -> str:
    """Tag of the agent image to start. Builds in the foreground only when no usable
    image exists for the current recipe (or pinned version); otherwise returns at
    once and leaves a version check to the detached worker."""
    recipe = spec_for(root, base).recipe()
    pinned = image.pinned_claude_code_version(root)
    cur = read_pointer(root)
    reason = _rebuild_reason(cur, recipe, pinned)
    if reason is not None or cur is None:
        return build(root, out, base, reason=reason or "no agent image built yet").tag
    building = _report(root, out, cur)
    if pinned is None and not building:
        spawn_refresh(root)
    return cur.tag


def spawn_refresh(root: Path) -> None:
    """Start the background worker fully detached: own session/process group, no
    terminal, output to a log. Best effort — the next start simply tries again."""
    log = _log_dir(root) / "image-refresh.log"
    pkg_parent = str(Path(__file__).resolve().parents[1])
    pythonpath = os.pathsep.join(p for p in (pkg_parent, os.environ.get("PYTHONPATH")) if p)
    env = dict(os.environ, PYTHONPATH=pythonpath)
    cmd = [sys.executable, "-m", "catraz.cli", "build", "--background", "-C", str(root)]
    kw: dict[str, Any] = {}
    if os.name == "nt":
        kw["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    else:
        kw["start_new_session"] = True
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "w") as f:
            subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT, env=env, **kw
            )
    except OSError:
        pass


def _say(msg: str) -> None:
    print(f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}", flush=True)


def refresh(root: Path) -> int:
    """Background worker body: build the published version if it is new, then switch."""
    if not _acquire_lock(root):
        _say("another agent-image build is running — nothing to do")
        return EXIT_OK
    try:
        return _refresh_locked(root)
    except CliError as e:
        _say(f"error: {e}")
        return e.code
    finally:
        _release_lock(root)


def _refresh_locked(root: Path) -> int:
    if image.pinned_claude_code_version(root) is not None:
        _say("CLAUDE_CODE_VERSION is pinned — nothing to do")
        return EXIT_OK
    spec = spec_for(root, image.resolve_base(root))
    recipe = spec.recipe()
    cur = read_pointer(root)
    if cur is None or cur.recipe != recipe:
        _say("no current image for this recipe — the next start builds it in the foreground")
        return EXIT_OK
    version = image.published_claude_code_version()
    if version is None:
        _say("registry unreachable — keeping claude-code " + cur.version)
        return EXIT_OK
    if version == cur.version:
        _failure_path(root).unlink(missing_ok=True)
        _say(f"claude-code {version} is current")
        return EXIT_OK
    _write_json(_lock_path(root), {"pid": os.getpid(), "version": version, "started": time.time()})
    tag = tag_for(root, recipe, version)
    log = _log_dir(root) / "image-build.log"
    _say(f"building claude-code {version} as {tag} (log: {log})")
    if not _docker_build(spec, recipe, version, tag, log=log):
        _write_json(_failure_path(root), {"version": version, "at": time.time()})
        _say("build failed — keeping claude-code " + cur.version)
        return EXIT_GENERAL
    latest = read_pointer(root)
    if latest is not None and latest.recipe != recipe:
        _say("recipe changed during the build — discarding " + tag)
        return EXIT_OK
    _write_pointer(root, Pointer(tag, recipe, version, new=True))
    _failure_path(root).unlink(missing_ok=True)
    _prune(root, keep=tag)
    _say(f"switched to claude-code {version}")
    return EXIT_OK


def rebuild(root: Path, out: Out, *, no_cache: bool = False) -> int:
    """Explicit `catraz build`: foreground, holding the lock so no worker races it."""
    if not _acquire_lock(root):
        out.err("a background agent-image build is running — retry when it finishes")
        return EXIT_GENERAL
    try:
        ptr = build(root, out, image.resolve_base(root), reason="requested", no_cache=no_cache)
    finally:
        _release_lock(root)
    out.info(out.green(f"• agent image ready: claude-code {ptr.version} ({ptr.tag})"))
    return EXIT_OK
