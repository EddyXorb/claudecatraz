"""Container test: three hosts (full default, review-only override, plain
type-cut) get different action gates from a real Warden container, on both
the git and REST axes.

Run: uv run --with pytest python -m pytest tests/container/test_multi_host_actions.py -q"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator, NamedTuple, Optional

import pytest

from catraz import compose


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    r = subprocess.run(["docker", "info"], capture_output=True)
    return r.returncode == 0


pytestmark = pytest.mark.skipif(not _docker_available(), reason="needs docker")


# ── fixed multi-endpoint fixture data (cascade example) ───────────────────────

HOST_FULL = "full-forge.test"  # gitlab, no `actions` override -> domain default
HOST_REVIEW = "review-forge.test"  # gitlab, actions = [repo.read, project.read, project.mr.comment]
HOST_PLAIN = "plain-forge.test"  # plain, no `actions` override -> default ∩ type
PROJECT = "acme/demo"

_WARDEN_TOML = f"""\
# Top-level (global) project allowlist: the request-path project gate for BOTH
# guards is `cfg.project_allowed` (guards/git/policy.py, core/guard.py) which
# reads this global list — the per-[[git.endpoint]] `allowed_projects` below is
# a config surface but is not (yet) what the git/REST project gate checks.
# Both must name the project for a request to clear the project gate and reach
# the upstream.
allowed_projects = ["{PROJECT}"]

[git]
# Domain default: the full built-in action set, spelled out explicitly so the
# FULL and PLAIN hosts' cascade below is visible in this file rather than
# relying on the code-side built-in default.
actions = ["repo.read", "repo.branch.create", "repo.branch.push",
           "project.read", "project.mr.create", "project.mr.edit",
           "project.mr.close", "project.mr.comment", "project.ci.trigger",
           "instance.projects.read", "instance.users.read", "instance.meta.read"]

[git.rules]
branch_prefixes = ["claude/"]

[[git.endpoint]]                        # full: no override -> domain default
host = "{HOST_FULL}"
type = "gitlab"
allowed_projects = ["{PROJECT}"]

[[git.endpoint]]                        # review-only: narrowing override
host = "{HOST_REVIEW}"
type = "gitlab"
allowed_projects = ["{PROJECT}"]
actions = ["repo.read", "project.read", "project.mr.comment"]

[[git.endpoint]]                        # plain: no override -> default ∩ type
host = "{HOST_PLAIN}"
type = "plain"
allowed_projects = ["{PROJECT}"]
"""

_READ_TOKENS = (
    f"{HOST_FULL}  dummy-read-full\n"
    f"{HOST_REVIEW}  dummy-read-review\n"
    f"{HOST_PLAIN}  dummy-read-plain\n"
)
_WRITE_TOKENS = (
    f"{HOST_FULL}  dummy-write-full\n"
    f"{HOST_REVIEW}  dummy-write-review\n"
    f"{HOST_PLAIN}  dummy-write-plain\n"
)


class Stack(NamedTuple):
    root: Path
    prefix: list[str]


def _row_ready(row: dict[str, str]) -> bool:
    state = (row.get("State") or "").lower()
    health = (row.get("Health") or "").lower()
    return state == "running" and health in ("", "healthy")


def _wait_healthy(root: Path, prefix: list[str], *, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = compose.compose_ps(root, prefix=prefix)
        if rows and all(_row_ready(r) for r in rows):
            return
        time.sleep(1)
    raise TimeoutError("gitlab-warden never became healthy")


@pytest.fixture(scope="module")
def live_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    """Scaffold a project via `catraz init`, overwrite it with a 3-endpoint
    `warden.toml` (cascade: full / review-only / plain) + grouped
    `read_tokens`/`write_tokens`, then bring up *only* `gitlab-warden` via
    `catraz.compose` — see module docstring for why."""
    root = tmp_path_factory.mktemp("catraz-multihost-actions")
    env = dict(os.environ, HOME=str(root))
    catraz_cli = [sys.executable, "-m", "catraz.cli"]

    subprocess.run(
        [*catraz_cli, "-C", str(root), "init", "-y", "--skip-sync"], env=env, check=False
    )  # exit 3 from doctor tolerated (no credentials yet) -- the scaffold is what we need

    (root / ".catraz" / "config" / "warden.toml").write_text(_WARDEN_TOML)
    secrets = root / ".catraz" / "secrets"
    (secrets / "read_tokens").write_text(_READ_TOKENS)
    (secrets / "write_tokens").write_text(_WRITE_TOKENS)
    (secrets / "read_tokens").chmod(0o600)
    (secrets / "write_tokens").chmod(0o600)

    prefix = compose.prepare(root, render=True)
    r = compose.run(root, ["up", "-d", "gitlab-warden"], prefix=prefix, check=False)
    assert r is not None and r.returncode == 0, "docker compose up gitlab-warden failed"
    _wait_healthy(root, prefix)

    yield Stack(root, prefix)

    compose.run(root, ["down", "--remove-orphans", "--volumes"], prefix=prefix, check=False)


def _probe(
    stack: Stack,
    *,
    host: str,
    path: str,
    method: str = "GET",
    body: Optional[dict[str, object]] = None,
) -> tuple[int, str]:
    """One request to the Warden's agent port (8080) from inside its own
    container, with an explicit Host header exercising the same host-routing
    lookup production uses. method/body drive REST writes like mr.create
    and mr.comment."""
    body_json = json.dumps(body) if body is not None else None
    script = (
        "import http.client, json\n"
        "c = http.client.HTTPConnection('127.0.0.1', 8080, timeout=15)\n"
        f"headers = {{'Host': {host + ':8080'!r}}}\n"
        f"body = {body_json!r}\n"
        "if body is not None:\n"
        "    headers['Content-Type'] = 'application/json'\n"
        f"c.request({method!r}, {path!r}, body=body, headers=headers)\n"
        "r = c.getresponse()\n"
        "resp_body = r.read().decode('utf-8', 'replace')\n"
        "print(json.dumps({'status': r.status, 'body': resp_body}))\n"
    )
    r = compose.run(
        stack.root,
        ["exec", "-T", "gitlab-warden", "python3", "-c", script],
        prefix=stack.prefix,
        capture=True,
        check=False,
    )
    assert r is not None and r.returncode == 0, f"docker compose exec failed: {r!r}"
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    payload = json.loads(lines[-1])
    status, body_out = payload["status"], payload["body"]
    assert isinstance(status, int) and isinstance(body_out, str)
    return status, body_out


def _git_advertise_path(project: str, *, push: bool) -> str:
    service = "git-receive-pack" if push else "git-upload-pack"
    return f"/git/{project}.git/info/refs?service={service}"


def _rest_mr_create_path(project: str) -> str:
    return f"/api/v4/projects/{project.replace('/', '%2F')}/merge_requests"


def _rest_mr_note_path(project: str, iid: int) -> str:
    return f"/api/v4/projects/{project.replace('/', '%2F')}/merge_requests/{iid}/notes"


def _rest_mr_diff_path(project: str, iid: int) -> str:
    return f"/api/v4/projects/{project.replace('/', '%2F')}/merge_requests/{iid}/diffs"


def _rest_mr_merge_path(project: str, iid: int) -> str:
    return f"/api/v4/projects/{project.replace('/', '%2F')}/merge_requests/{iid}/merge"


def _assert_routed(status: int, body: str, host: str, label: str) -> None:
    """Cleared every gate (host + project + action) and reached its (absent)
    mock upstream -> deterministic 500. A plain "not 403" would be too weak;
    500 specifically proves "routed", not merely "not denied"."""
    assert status == 500, (
        f"{label}: host {host!r} expected to route to its (absent) upstream -> 500, "
        f"got {status}: {body}"
    )


def _assert_denied(status: int, body: str, *, reason_contains: list[str]) -> None:
    assert status == 403, f"expected deny, got {status}: {body}"
    payload = json.loads(body)
    for needle in reason_contains:
        assert needle in payload["reason"], f"expected {needle!r} in reason, got {payload!r}"


def _assert_action_gate_cleared_but_state_locked(status: int, body: str, host: str) -> None:
    """A write whose action is enabled still 403s here: with no real forge,
    MR-quota reconcile never finishes, so state stays fail-closed-locked and
    the request dies on that check before forward(). That "state locked"
    deny is itself proof the action gate passed — a disabled action denies
    "not enabled for host" first, before quota state is checked."""
    assert status == 403, f"host {host!r}: expected state-locked deny, got {status}: {body}"
    payload = json.loads(body)
    assert "state locked" in payload["reason"], (
        f"expected 'state locked' in reason, got {payload!r}"
    )


@pytest.mark.slow
def test_full_endpoint_push_and_mr_create_routed(live_stack: Stack) -> None:
    """Full default: no `actions` override inherits both `repo.branch.push`
    and `project.mr.create` from the domain default. Advertise routes (500);
    mr.create hits the state-locked deny instead
    (see `_assert_action_gate_cleared_but_state_locked`)."""
    status, body = _probe(live_stack, host=HOST_FULL, path=_git_advertise_path(PROJECT, push=True))
    _assert_routed(status, body, HOST_FULL, "push discovery (advertise-receive)")

    status, body = _probe(
        live_stack,
        host=HOST_FULL,
        path=_rest_mr_create_path(PROJECT),
        method="POST",
        body={"source_branch": "claude/x", "target_branch": "main"},
    )
    _assert_action_gate_cleared_but_state_locked(status, body, HOST_FULL)


@pytest.mark.slow
def test_review_only_endpoint_narrows_selectively(live_stack: Stack) -> None:
    """Review-only override (`repo.read`, `project.read`,
    `project.mr.comment`) narrows selectively: discovery and MR diffs stay
    routed, `mr.create` is denied (not enabled for host), `mr.comment` still
    routes, and merge is denied regardless of config (criticality gate)."""
    status, body = _probe(
        live_stack, host=HOST_REVIEW, path=_git_advertise_path(PROJECT, push=False)
    )
    _assert_routed(status, body, HOST_REVIEW, "fetch discovery (advertise-upload)")

    status, body = _probe(
        live_stack, host=HOST_REVIEW, path=_git_advertise_path(PROJECT, push=True)
    )
    _assert_routed(status, body, HOST_REVIEW, "push discovery (advertise-receive)")

    status, body = _probe(live_stack, host=HOST_REVIEW, path=_rest_mr_diff_path(PROJECT, 1))
    _assert_routed(status, body, HOST_REVIEW, "project.read (mr diff)")

    status, body = _probe(
        live_stack,
        host=HOST_REVIEW,
        path=_rest_mr_create_path(PROJECT),
        method="POST",
        body={"source_branch": "claude/x", "target_branch": "main"},
    )
    _assert_denied(
        status,
        body,
        reason_contains=["project.mr.create", "not enabled", HOST_REVIEW],
    )

    status, body = _probe(
        live_stack,
        host=HOST_REVIEW,
        path=_rest_mr_note_path(PROJECT, 1),
        method="POST",
        body={"body": "hi"},
    )
    _assert_routed(status, body, HOST_REVIEW, "project.mr.comment (mr.note)")

    status, body = _probe(
        live_stack, host=HOST_REVIEW, path=_rest_mr_merge_path(PROJECT, 1), method="PUT"
    )
    _assert_denied(status, body, reason_contains=["irreversible"])


@pytest.mark.slow
def test_plain_endpoint_fetch_and_push_routed(live_stack: Stack) -> None:
    """Plain type-cut: no `actions` override still lets both git transport
    ops route; a `plain` host has no REST base to probe."""
    status, body = _probe(
        live_stack, host=HOST_PLAIN, path=_git_advertise_path(PROJECT, push=False)
    )
    _assert_routed(status, body, HOST_PLAIN, "fetch discovery (advertise-upload)")

    status, body = _probe(live_stack, host=HOST_PLAIN, path=_git_advertise_path(PROJECT, push=True))
    _assert_routed(status, body, HOST_PLAIN, "push discovery (advertise-receive)")
