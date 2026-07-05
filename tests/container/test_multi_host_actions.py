"""Container-level integration test for endpoint actions: a multi-endpoint
deployment with three hosts — full default `actions`, review-only override,
and a `plain`-type host inheriting the default's type-cut — is actually
treated differently by a *real* Warden container, on both the git axis and
the REST axis.

Same blueprint as ``tests/container/test_multi_host.py``: a real
``gitlab-warden`` service (via `catraz.compose`), driven with `docker compose
exec` + a stdlib `http.client` call carrying an explicit `Host` header — no
unit-level mock, no real forge behind these hostnames (see that module's
docstring for the full rationale, not repeated here).

Run (needs Docker):
    uv run --with pytest python -m pytest tests/container/test_multi_host_actions.py -q
"""

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
    """One request straight to the Warden's agent port (8080) from *inside*
    its own running container, with an explicit `Host` header — exercising
    exactly the `request.headers["host"]` lookup
    `core.guard.host_gate`/`core.transport.UpstreamRouter` perform in
    production (see `test_multi_host.py`'s module docstring for why this
    replaces a DNS-alias hop through a live agent container here).

    ``method``/``body`` extend `test_multi_host.py`'s GET-only probe: a JSON
    body is needed to drive `mr.create`/`mr.comment` REST writes.
    """
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
    """The request cleared every gate (host + project + action) and reached
    out to its upstream (`forward()`, or — for a REST comment, whose
    namespace check needs an iid -> MR upstream lookup in `enrich()` — that
    lookup) — proof the action *was* enabled for this host. With no real
    forge behind these mock hostnames, that outbound call then fails to
    connect, which surfaces as a deterministic 500 (verified live, same
    mechanism as `test_multi_host.py`'s `_assert_routed`). A plain "not 403"
    would be too weak (a 5xx from some other bug would also pass); the 500
    pins "routed, upstream just absent"."""
    assert status == 500, (
        f"{label}: host {host!r} expected to route to its (absent) upstream -> 500, "
        f"got {status}: {body}"
    )


def _assert_denied(status: int, body: str, *, rule: str, reason_contains: list[str]) -> None:
    assert status == 403, f"expected {rule} deny, got {status}: {body}"
    payload = json.loads(body)
    assert payload["rule"] == rule, f"expected rule {rule}, got {payload!r}"
    for needle in reason_contains:
        assert needle in payload["reason"], f"expected {needle!r} in reason, got {payload!r}"


def _assert_action_gate_cleared_but_state_locked(status: int, body: str, host: str) -> None:
    """A matched write recognizer (e.g. `mr.create`) whose *action* is enabled
    for `host` still cannot reach `forward()` in this test's environment: the
    REST-API guard's own MR-quota reconcile (`guards.git.gitlab.reconcile`)
    calls out to the (deliberately absent, per "Nicht tun" — no real forge)
    upstream at boot for every allowed project on every configured host, and
    `core.transport.for_each_host_project` leaves the guard's state
    fail-closed-**locked** forever if even one of those calls ever raises
    (verified live: it always does here, since none of the three hosts have a
    real upstream) — a structural side effect of the no-real-forge
    constraint, orthogonal to the actions mechanism this test is about.

    That lock is checked in `policy._quota_check`, reached only *after* a
    write has already matched a recognizer in the host's effective table
    (`policy.decide` -> `decide_scope`) — a write whose action is *not*
    enabled for the host never gets that far; it default-denies with R3
    first (see `test_review_only_endpoint_narrows_selectively`'s `mr.create`
    assertion). So R5 "state locked" here is itself the proof the action gate
    passed for `mr.create` on this host: the *only* other way to reach this
    exact deny is R3, and that is a different action-gate outcome entirely.
    """
    assert status == 403, f"host {host!r}: expected R5 state-locked deny, got {status}: {body}"
    payload = json.loads(body)
    assert payload["rule"] == "R5", f"expected rule R5, got {payload!r}"
    assert "state locked" in payload["reason"], (
        f"expected 'state locked' in reason, got {payload!r}"
    )


@pytest.mark.slow
def test_full_endpoint_push_and_mr_create_routed(live_stack: Stack) -> None:
    """Full default: a `gitlab` endpoint with no `actions` override inherits
    the domain default, which includes both `repo.branch.push` and
    `project.mr.create` — both clear the action gate.

    Advertise (either service) always recognizes to `repo.read` — it never
    touches quota state, so it reaches `forward()` and gets this test's usual
    "routed" 500 (absent upstream). `mr.create` does consult quota state
    first — and in this environment (deliberately no real forge behind any
    of the three hosts) that state can never finish reconciling, so it
    denies R5 "state locked" instead of reaching `forward()`; see
    `_assert_action_gate_cleared_but_state_locked` for why that is still the
    correct, specific proof that the action gate passed (as opposed to
    `review-only`'s R6 "not enabled" for the same request shape).
    """
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
    """Review-only override: `actions = ["repo.read", "project.read",
    "project.mr.comment"]` narrows this host relative to the full default —
    but selectively, not blanket:

    - Fetch discovery stays allowed (routed) — `repo.read` is enabled.
    - Push discovery (advertise-receive) *also* routes: advertise always
      recognizes to `repo.read` regardless of service, by design (main
      document §6) — a disabled push denies at `receive-pack`, per ref, not
      at discovery. This test only probes discovery, so it cannot observe
      that denial directly; it is covered at the unit level in
      `warden/tests/transport/test_recognizers.py` and `test_policy.py`, and
      through a real `git push` in `warden/tests/test_git_e2e.py`.
    - MR diffs route (`project.read` is enabled; the diff endpoint falls
      through the same catch-all read recognizer as any other project read —
      a deliberate carve-out: MR diffs are visible under `project.read` alone,
      with no `repo.read` needed).
    - `project.mr.create` is denied (not in this host's effective actions) —
      same status/rule as
      ``test_two_hosts_with_different_actions_behave_differently_on_the_same_guard``
      in ``warden/tests/test_api_proxy.py`` (403, R6).
    - `project.mr.comment` (a `mr.note` recognizer) stays allowed (routed) —
      proving the narrowing is selective: two REST writes on the very same
      host, one denied and one allowed, per the configured `actions` list
      alone.
    - Merge is denied regardless of config (R4, the criticality gate) — a
      config that could never enable it in the first place, unlike R6.

    A real, in-container push denial and an "API-with-repo.read-off" probe
    (repo.read disabled while project.read stays on) are deliberately not
    repeated here: both are already proven end-to-end by `full_decide`-based
    unit tests (`warden/tests/redteam/test_bypass.py`, `warden/tests/gitlab/
    test_recognizers.py`) that exercise the identical real parse -> recognize
    -> kernel_gates -> decide pipeline this container also runs — adding a
    fourth docker endpoint (or raw pkt-line-over-`docker compose exec`
    plumbing) here would only re-prove wiring already covered by the git
    advertise/REST probes above, not anything genuinely uncovered.
    """
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
        rule="R6",
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
    _assert_denied(status, body, rule="R4", reason_contains=["irreversible"])


@pytest.mark.slow
def test_plain_endpoint_fetch_and_push_routed(live_stack: Stack) -> None:
    """Plain, inherited type-cut: a `plain`-type endpoint with no `actions`
    override inherits the domain default intersected with its type's
    vocabulary (`repo.read`/`repo.branch.create`/`repo.branch.push` — no
    forge/REST actions at all). Spot-check only: both git transport
    operations still clear the action gate and are routed; there is no
    meaningful REST `mr.*` path on a `plain` host to probe (`type = "plain"`
    has no REST base at all, `core.transport.base_urls`)."""
    status, body = _probe(
        live_stack, host=HOST_PLAIN, path=_git_advertise_path(PROJECT, push=False)
    )
    _assert_routed(status, body, HOST_PLAIN, "fetch discovery (advertise-upload)")

    status, body = _probe(live_stack, host=HOST_PLAIN, path=_git_advertise_path(PROJECT, push=True))
    _assert_routed(status, body, HOST_PLAIN, "push discovery (advertise-receive)")
