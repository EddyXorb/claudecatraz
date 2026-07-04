"""Container-level integration test for Doc 08 (multi-target) §8 / step 08
(docs/design/architecture-generalization/08-multi-target/08-container-test.md):
one `.catraz` serves TWO listed hosts through the same Warden and rejects a
THIRD, unlisted host (R6 default-deny) — plus the optional closed-endpoint
case (§4.2 fail-closed-degrade).

This is the compose-wired complement to the unit tests of steps 01-07: it
runs a *real* Warden container (not a python-level mock) and exercises the
actual `request.headers["host"]` routing code path
(`core.guard.host_gate`/`core.transport.UpstreamRouter`) over the network.

Deliberately does NOT reuse `tests/redteam/test_shadow_mount.py`'s /
`test_agent_adapter.py`'s `live_stack` fixture (`catraz run claude-remote`
against `GITLAB_READ_TOKEN`/`GITLAB_WRITE_TOKEN`/`WARDEN_ALLOWED_PROJECTS`):
that config shape was removed by steps 02/05 of this plan, and two more
things about that fixture don't hold up under an actual run (confirmed by
running it in this sandbox while building this test):

  1. ``[sys.executable, "-m", "catraz"]`` fails — the ``catraz`` package has
     no ``__main__.py``; only ``catraz.cli:main``, wired as the ``catraz``
     console-script (``pyproject.toml``'s ``[project.scripts]``). This test
     uses ``python -m catraz.cli`` instead, which does work.
  2. ``run claude-remote`` starts Remote Control, which requires a *real*
     claude.ai subscription login; with a dummy API key the agent container
     exits 1 immediately ("You must be logged in to use Remote Control"), so
     that fixture cannot stay up here regardless of Docker availability.

Host routing is entirely a Warden concern (§1-§2 of the main doc), so this
test starts only the `gitlab-warden` service directly via `catraz.compose`
(the same module the CLI itself uses to run docker compose) and drives
requests via `docker compose exec` into that container — a stdlib
`http.client` call with an explicit `Host` header — instead of needing a live
agent container at all. This is the exact `request.headers["host"]` the
production DNS-alias path (compose-wired per-host aliases, step 07) feeds in
a real deployment; sending it directly here proves the Warden's own
host-routing/deny behaviour without depending on that (here unusable) agent
image, matching this task's guidance that the assertions are about warden
routing/deny behavior, not real forge auth.

Run (needs Docker):
    uv run --with pytest python -m pytest tests/container/test_multi_host.py -q
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator, NamedTuple

import pytest

from catraz import compose


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    r = subprocess.run(["docker", "info"], capture_output=True)
    return r.returncode == 0


pytestmark = pytest.mark.skipif(not _docker_available(), reason="needs docker")


# ── fixed multi-endpoint fixture data ─────────────────────────────────────────

HOST_A = "mock-forge-a.test"  # listed, read+write tokens -> open
HOST_B = "mock-forge-b.test"  # listed, read+write tokens -> open
HOST_CLOSED = "mock-forge-closed.test"  # listed, NO token at all -> closed (§4.2)
HOST_UNLISTED = "unlisted-forge.test"  # never in warden.toml at all
PROJECT = "acme/demo"

_WARDEN_TOML = f"""\
# Top-level (global) project allowlist: the request-path project gate for BOTH
# guards is `cfg.project_allowed` (guards/git/policy.py, core/guard.py) which
# reads this global list — the per-[[git.endpoint]] `allowed_projects` below is
# the §3 config surface but is not (yet) what the git/REST project gate checks.
# Both must name the project for a request to clear the project gate and reach
# the upstream.
allowed_projects = ["{PROJECT}"]

[git.rules]
branch_prefixes = ["claude/"]

[[git.endpoint]]
host = "{HOST_A}"
type = "gitlab"
allowed_projects = ["{PROJECT}"]

[[git.endpoint]]
host = "{HOST_B}"
type = "gitlab"
allowed_projects = ["{PROJECT}"]

[[git.endpoint]]
host = "{HOST_CLOSED}"
type = "gitlab"
allowed_projects = ["{PROJECT}"]
"""

_READ_TOKENS = f"{HOST_A}  dummy-read-a\n{HOST_B}  dummy-read-b\n"
_WRITE_TOKENS = f"{HOST_A}  dummy-write-a\n{HOST_B}  dummy-write-b\n"


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
    `warden.toml` + grouped `read_tokens`/`write_tokens` (§3, §4.1), then
    bring up *only* `gitlab-warden` (no forward-proxy, no agent) via
    `catraz.compose` — see module docstring for why."""
    root = tmp_path_factory.mktemp("catraz-multihost")
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


def _probe(stack: Stack, *, host: str, path: str) -> tuple[int, str]:
    """One GET request straight to the Warden's agent port (8080) from
    *inside* its own running container, with an explicit `Host` header —
    exercising exactly the `request.headers["host"]` lookup
    `core.guard.host_gate`/`core.transport.UpstreamRouter` perform in
    production (see module docstring for why this replaces a DNS-alias hop
    through a live agent container here)."""
    script = (
        "import http.client, json\n"
        "c = http.client.HTTPConnection('127.0.0.1', 8080, timeout=15)\n"
        f"c.request('GET', {path!r}, headers={{'Host': {host + ':8080'!r}}})\n"
        "r = c.getresponse()\n"
        "body = r.read().decode('utf-8', 'replace')\n"
        "print(json.dumps({'status': r.status, 'body': body}))\n"
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
    status, body = payload["status"], payload["body"]
    assert isinstance(status, int) and isinstance(body, str)
    return status, body


def _git_path(project: str) -> str:
    return f"/git/{project}.git/info/refs?service=git-upload-pack"


def _rest_path(project: str) -> str:
    return f"/api/v4/projects/{project.replace('/', '%2F')}"


def _assert_routed(status: int, body: str, host: str) -> None:
    """The request cleared *every* gate (host + project + decide) and reached
    `forward()` — proof the host was routed to its upstream. With no real forge
    behind these mock hostnames (§"Nicht tun": no real instances) `forward()`
    then fails to connect, which surfaces as a deterministic 500 (verified
    live). Any 403 here would mean a gate denied it (host OR project — both use
    rule R6), so a plain "not 403" is too weak; the 500 pins "routed, upstream
    just absent"."""
    assert status == 500, (
        f"host {host!r} expected to route to its (absent) upstream -> 500, got {status}: {body}"
    )


def _assert_host_denied(status: int, body: str, host: str) -> None:
    assert status == 403, f"expected R6 default-deny for {host!r}, got {status}: {body}"
    payload = json.loads(body)
    assert payload["rule"] == "R6"
    assert "multi-target allowlist" in payload["reason"]
    assert host in payload["reason"]


@pytest.mark.slow
def test_two_listed_hosts_reachable_git_and_rest(live_stack: Stack) -> None:
    """§8 Umsetzung point 2: both configured hosts are routed by the same
    Warden — a git-path *and* a REST-path request each clear `host_gate` for
    *both* hosts independently (per-endpoint separation, step 04,
    spot-checked here by hostA's outcome never depending on hostB's, and
    vice versa — a single shared Warden process, no per-host guard copy)."""
    for host in (HOST_A, HOST_B):
        status, body = _probe(live_stack, host=host, path=_git_path(PROJECT))
        _assert_routed(status, body, host)

        status, body = _probe(live_stack, host=host, path=_rest_path(PROJECT))
        _assert_routed(status, body, host)


@pytest.mark.slow
def test_third_unlisted_host_default_denied(live_stack: Stack) -> None:
    """§8 Umsetzung point 3: a Host header naming an endpoint nowhere in
    warden.toml is R6 default-denied, for both git and REST paths."""
    status, body = _probe(live_stack, host=HOST_UNLISTED, path=_git_path(PROJECT))
    _assert_host_denied(status, body, HOST_UNLISTED)

    status, body = _probe(live_stack, host=HOST_UNLISTED, path=_rest_path(PROJECT))
    _assert_host_denied(status, body, HOST_UNLISTED)


@pytest.mark.slow
def test_closed_endpoint_denied_without_disturbing_other_endpoints(live_stack: Stack) -> None:
    """§8 Umsetzung point 4 (optional) + §4.2 fail-closed-degrade: a listed
    endpoint with no token at all starts `closed` — denied exactly like an
    unlisted host (the deny response deliberately does not distinguish
    "known but closed" from "never heard of", so it never leaks which hosts
    are configured) — *without* taking the Warden or the other, healthy
    endpoints down with it."""
    status, body = _probe(live_stack, host=HOST_CLOSED, path=_git_path(PROJECT))
    _assert_host_denied(status, body, HOST_CLOSED)

    # The other endpoints must be entirely unaffected by hostC's bad config.
    status, body = _probe(live_stack, host=HOST_A, path=_git_path(PROJECT))
    _assert_routed(status, body, HOST_A)
