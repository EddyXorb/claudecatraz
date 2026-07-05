"""Red-team bypass attempts (W14, §8.2) at the HTTP boundary — the cheap, no-docker
subset: merge aliases, prefix tricks, cross-project reads, API branch creation.

The full hostile-agent docker-compose suite (§8.2: printenv has no token, no direct
connect, flooding, exfil) is environment-level and lives outside this unit suite.

Later sections extend this file for the actions-rework fail-closed edges: field-
conditional smuggling (state_event, search scope), the repo/project content line,
push-batch atomicity/quota, config-time rejection of retired action ids, GraphQL
across every method, and unmodelled endpoints.
"""

from __future__ import annotations

import httpx
import pytest

from warden.core.config import Config, ConfigError, GitEndpoint, HostCredentials
from warden.core.config_load import from_env
from warden.core.model import StateView
from warden.guards.git.gitlab.intent import ApiIntent
from warden.guards.git.gitlab.policy import full_decide
from warden.guards.git.transport.pktline import FLUSH, pkt_line
from warden.guards.git.transport.state import BranchState

PROJ = "group%2Fproj"
ZERO = "0" * 40
SHA1 = "1" * 40


async def test_merge_alias_when_pipeline_succeeds_blocked(client, respx_router):
    resp = await client.put(
        f"/api/v4/projects/{PROJ}/merge_requests/7/merge?merge_when_pipeline_succeeds=true"
    )
    assert resp.status_code == 403 and resp.json()["rule"] == "R4"


async def test_merge_via_state_event_blocked(client, respx_router):
    # Foreign author but namespace source_branch — §07 Punkt 4 allows touching the MR,
    # but R4's merge block is independent and must still apply.
    respx_router.route(method="GET", url__regex=r".*/merge_requests/7$").mock(
        return_value=httpx.Response(200, json={"source_branch": "claude/x", "author": {"id": 999}})
    )
    resp = await client.put(
        f"/api/v4/projects/{PROJ}/merge_requests/7", json={"state_event": "merge"}
    )
    assert resp.status_code == 403 and resp.json()["rule"] == "R4"


async def test_mr_note_on_non_namespace_branch_still_denied(client, respx_router):
    # The security boundary that matters (§07 Punkt 4): dropping the author check
    # must NOT also drop the namespace check. A MR whose source_branch is outside
    # the allowed prefixes stays blocked no matter who authored it.
    respx_router.route(method="GET", url__regex=r".*/merge_requests/7$").mock(
        return_value=httpx.Response(200, json={"source_branch": "feature/x", "author": {"id": 42}})
    )
    resp = await client.post(f"/api/v4/projects/{PROJ}/merge_requests/7/notes", json={"body": "hi"})
    assert resp.status_code == 403 and resp.json()["rule"] == "R3"


async def test_cross_project_read_blocked(client, respx_router):
    resp = await client.get("/api/v4/projects/secret%2Fdata/repository/files/x")
    assert resp.status_code == 403 and resp.json()["rule"] == "R6"


# --- B1: "content, not visibility" — projectless content-capable reads must
# stay blocked no matter how they're dressed up, even though the token can
# technically see every project. ---
async def test_global_blob_search_cannot_harvest_code_across_projects(client, respx_router):
    # The whole point of B1: global search with scope=blobs returns code content
    # from *every* project visible to the token, bypassing allowed_projects.
    resp = await client.get("/api/v4/search?scope=blobs&search=password")
    assert resp.status_code == 403 and resp.json()["rule"] == "R6"


async def test_group_commit_search_cannot_harvest_content(client, respx_router):
    resp = await client.get("/api/v4/groups/1/search?scope=commits&search=secret")
    assert resp.status_code == 403 and resp.json()["rule"] == "R6"


async def test_snippets_cannot_be_read_projectless(client, respx_router):
    resp = await client.get("/api/v4/snippets")
    assert resp.status_code == 403 and resp.json()["rule"] == "R6"


async def test_group_discovery_still_works_despite_b1(client, respx_router):
    # B1 must not regress the AGENT.md-documented discovery flow: names/metadata
    # stay readable, only content is scoped.
    respx_router.route(method="GET", url__regex=r".*/groups/.*/projects$").mock(
        return_value=httpx.Response(200, json=[{"name": "proj"}])
    )
    resp = await client.get("/api/v4/groups/1/projects")
    assert resp.status_code == 200


# --- B5: GraphQL must never become a policy bypass -----------------------------
async def test_graphql_mutation_cannot_bypass_merge_block(client, respx_router):
    # A GraphQL mutation could merge an MR in one call, bypassing R4 entirely —
    # the warden must refuse before any upstream contact, not rely on GitLab.
    resp = await client.post(
        "/api/graphql",
        json={"query": "mutation { mergeRequestAccept(input: {}) { errors } }"},
    )
    assert resp.status_code == 403 and resp.json()["rule"] == "R6"


async def test_graphql_read_query_also_denied(client, respx_router):
    resp = await client.get("/api/graphql")
    assert resp.status_code == 403 and resp.json()["rule"] == "R6"


async def test_api_branch_creation_outside_namespace_denied(client, respx_router):
    # repo.branch.create is default-on (one knob covers both the git-push and
    # REST wires) — the namespace boundary (R2) is what must still hold for a
    # branch name outside the allowed prefixes.
    resp = await client.post(
        f"/api/v4/projects/{PROJ}/repository/branches",
        json={"branch": "main", "ref": "main"},
    )
    assert resp.status_code == 403 and resp.json()["rule"] == "R2"


async def test_api_branch_creation_in_namespace_allowed_by_default(client, respx_router):
    respx_router.route(method="POST", url__regex=r".*/repository/branches$").mock(
        return_value=httpx.Response(201, json={"name": "claude/x"})
    )
    resp = await client.post(
        f"/api/v4/projects/{PROJ}/repository/branches",
        json={"branch": "claude/x", "ref": "main"},
    )
    assert resp.status_code == 201


async def test_push_prefix_lookalike_blocked(client, respx_router):
    # "claudex/feature" shares the leading "claude" but misses the slash
    # separator, so it must NOT satisfy the "claude/" prefix.
    body = (
        pkt_line(f"{ZERO} {SHA1} refs/heads/claudex/feature\x00report-status\n".encode())
        + FLUSH
        + b"PACK"
    )
    resp = await client.post("/git/group/proj.git/git-receive-pack", content=body)
    assert b"warden: R2" in resp.content


async def test_git_cross_project_push_blocked(client, respx_router):
    body = (
        pkt_line(f"{ZERO} {SHA1} refs/heads/claude/x\x00report-status\n".encode()) + FLUSH + b"PACK"
    )
    resp = await client.post("/git/other/secret.git/git-receive-pack", content=body)
    assert b"warden: R6" in resp.content


# --- state_event smuggling: fail-closed field-conditional mapping -------------


async def test_state_event_unknown_value_denied(client, respx_router):
    resp = await client.put(
        f"/api/v4/projects/{PROJ}/merge_requests/7", json={"state_event": "bogus"}
    )
    assert resp.status_code == 403 and resp.json()["rule"] == "R3"


@pytest.mark.parametrize("value", ["Merge", "MERGE", "Close", "REOPEN"])
async def test_state_event_casing_variant_denied(client, respx_router, value):
    # The lookup is a plain dict keyed on the lowercase GitLab values — a
    # differently-cased known value is therefore case-sensitively rejected,
    # not silently folded to its lowercase counterpart.
    resp = await client.put(
        f"/api/v4/projects/{PROJ}/merge_requests/7", json={"state_event": value}
    )
    assert resp.status_code == 403 and resp.json()["rule"] == "R3"


async def test_state_event_in_query_string_is_not_read_declared_location_wins(client, respx_router):
    # decision_fields declares state_event as BODY-only; a query-string value
    # must be invisible to the recognizer. If it leaked in, this would be a
    # merge attempt (R4, compiled-in deny); instead it falls through as a plain
    # edit (no state_event at all) and is forwarded.
    respx_router.route(method="GET", url__regex=r".*/merge_requests/7$").mock(
        return_value=httpx.Response(200, json={"source_branch": "claude/x"})
    )
    respx_router.route(method="PUT", url__regex=r".*/merge_requests/7\?state_event=merge$").mock(
        return_value=httpx.Response(200, json={"iid": 7})
    )
    resp = await client.put(f"/api/v4/projects/{PROJ}/merge_requests/7?state_event=merge", json={})
    assert resp.status_code == 200


# --- search scope fuzz: unknown/missing/content scopes all deny --------------


async def test_search_unknown_scope_denied(client, respx_router):
    resp = await client.get("/api/v4/search?scope=nonexistent_scope")
    assert resp.status_code == 403 and resp.json()["rule"] == "R6"


async def test_search_missing_scope_denied(client, respx_router):
    resp = await client.get("/api/v4/search")
    assert resp.status_code == 403 and resp.json()["rule"] == "R6"


async def test_search_wiki_blobs_scope_cannot_harvest_content(client, respx_router):
    resp = await client.get("/api/v4/search?scope=wiki_blobs&search=secret")
    assert resp.status_code == 403 and resp.json()["rule"] == "R6"


# --- the repo/project content line: repo.read off, project.read on ----------
# blobs/commits/wiki_blobs above prove the *scope* boundary; these prove the
# content-line boundary is a second, independent axis: with repo.read
# disabled, every content-capable project-bound read is denied even though
# project.read (attributes/MRs/issues) stays enabled — except the deliberate
# MR-diff carve-out, which still passes under project.read alone.

_CL_HOST = "gitlab.example"


def _content_line_cfg() -> Config:
    return Config(
        allowed_projects=("group/proj",),
        git_endpoints=(GitEndpoint(host=_CL_HOST, type="gitlab"),),
        git_credentials={_CL_HOST: HostCredentials(read_token="r", write_token="w")},
    )


def _content_line_intent(path: str) -> ApiIntent:
    return ApiIntent(_project="group/proj", _method="GET", path=path, _host=_CL_HOST)


@pytest.mark.parametrize(
    "path",
    [
        "/projects/group%2Fproj/repository/files/README.md/raw",
        "/projects/group%2Fproj/jobs/9/artifacts/build/out.zip",
        "/projects/group%2Fproj/snippets/5/raw",
    ],
)
def test_content_line_denied_when_repo_read_off_project_read_on(path):
    d = full_decide(
        _content_line_intent(path),
        StateView(),
        _content_line_cfg(),
        frozenset({"project.read", "instance.projects.read"}),
    )
    assert not d.allow


def test_content_line_mr_diff_still_passes_when_repo_read_off():
    d = full_decide(
        _content_line_intent("/projects/group%2Fproj/merge_requests/7/diffs"),
        StateView(),
        _content_line_cfg(),
        frozenset({"project.read"}),
    )
    assert d.allow and d.rule == "R1"


# --- push batch: one forbidden ref poisons the whole batch; quota is a -------
# separate rule id from the action gate --------------------------------------


async def test_push_batch_tag_create_poisons_an_otherwise_fine_batch(client, respx_router):
    # claude/good is an ordinary, in-namespace branch create (fine on its own);
    # claude/v1 is a tag create (IRREVERSIBLE, compiled-in deny). The kernel's
    # criticality gate sees the *union* of the batch's recognized actions, so
    # the tag poisons the whole push — the good ref is rejected too, not just
    # the bad one.
    body = (
        pkt_line(f"{ZERO} {SHA1} refs/heads/claude/good\x00report-status\n".encode())
        + pkt_line(f"{ZERO} {SHA1} refs/tags/claude/v1\n".encode())
        + FLUSH
        + b"PACK"
    )
    resp = await client.post("/git/group/proj.git/git-receive-pack", content=body)
    assert resp.content.count(b"ng ") == 2
    assert resp.content.count(b"warden: R4") == 2


async def test_push_batch_n_creates_against_quota_of_n_minus_1_rejected(
    client, cfg, state, respx_router
):
    # Quota (R5), not the action gate (R6): repo.branch.create is enabled for
    # every ref here — the batch is rejected purely because it would push the
    # open-branch count past max_open_branches.
    host = cfg.git_endpoints[0].host
    bs = BranchState(state.store)
    for i in range(cfg.max_open_branches - 1):
        bs.add_branch(host, "group/proj", f"claude/existing-{i}")

    body = (
        pkt_line(f"{ZERO} {SHA1} refs/heads/claude/new-a\x00report-status\n".encode())
        + pkt_line(f"{ZERO} {SHA1} refs/heads/claude/new-b\n".encode())
        + FLUSH
        + b"PACK"
    )
    resp = await client.post("/git/group/proj.git/git-receive-pack", content=body)
    assert b"warden: R5" in resp.content


# --- old ids in config: ConfigError at startup, never silently accepted -----


def test_old_domain_action_id_aborts_startup(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text('[git]\nactions = ["git.push", "mr.update"]\n')
    with pytest.raises(ConfigError, match="unknown action id"):
        from_env({}, strict=True, toml_path=str(toml))


def test_old_endpoint_action_id_aborts_startup(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text(
        '[[git.endpoint]]\nhost = "gitlab.com"\ntype = "gitlab"\nactions = ["git.fetch"]\n'
    )
    with pytest.raises(ConfigError, match="unknown action id"):
        from_env({}, strict=True, toml_path=str(toml))


# --- GraphQL: denied on every HTTP method, not just GET/POST -----------------


@pytest.mark.parametrize("method", ["put", "delete", "patch"])
async def test_graphql_denied_on_every_method_via_app_client(client, respx_router, method):
    resp = await getattr(client, method)("/api/graphql")
    assert resp.status_code == 403 and resp.json()["rule"] == "R6"


# --- newly-named surface: MR pipeline trigger gated like any other action ---


def test_mr_pipeline_trigger_denied_when_ci_trigger_disabled():
    intent = ApiIntent(
        _project="group/proj",
        _method="POST",
        path="/projects/group%2Fproj/merge_requests/7/pipelines",
        _host=_CL_HOST,
    )
    d = full_decide(
        intent,
        StateView(),
        _content_line_cfg(),
        frozenset({"repo.read", "project.read"}),  # project.ci.trigger NOT enabled
    )
    assert not d.allow and d.rule == "R6"
    assert "project.ci.trigger" in d.reason


# --- still-unmodelled surface: pipeline DELETE has no recognizer at all -----


async def test_pipeline_delete_is_unmodelled_and_denied(client, respx_router):
    resp = await client.delete(f"/api/v4/projects/{PROJ}/pipelines/9")
    assert resp.status_code == 403 and resp.json()["rule"] == "R3"
