"""The kernel's action gates: criticality, enablement, and per-type
action-id derivation. Pins down behavior shared across guards once an
action is recognized, plus proof a denied action never reaches enrich.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from warden.app import create_app
from warden.context import build_context
from warden.core.actions import Action, Criticality
from warden.core.audit import AuditLog
from warden.core.config import Config, ConfigError, GitEndpoint, HostCredentials
from warden.core.config_load import from_env
from warden.core.guard import action_gate, criticality_gate
from warden.core.model import StateView
from warden.core.state import State
from warden.guards.git import actions as git_actions
from warden.guards.git.endpoints import ENDPOINT_TYPES
from warden.guards.git.gitlab import actions as gitlab_actions
from warden.guards.git.gitlab.intent import ApiIntent
from warden.guards.git.gitlab.policy import full_decide as api_full_decide
from warden.guards.git.transport import actions as transport_actions
from warden.guards.git.transport.intent import GitIntent
from warden.guards.git.transport.pktline import RefCommand
from warden.guards.git.transport.policy import full_decide as git_full_decide

HOST = "gitlab.example"
ZERO = "0" * 40
SHA = "a" * 40
_OPEN_CREDENTIALS = {HOST: HostCredentials(read_token="r", write_token="w")}


def _cfg(actions: tuple[str, ...] | None = None) -> Config:
    return Config(
        allowed_projects=("group/proj",),
        git_endpoints=(GitEndpoint(host=HOST, type="gitlab", actions=actions),),
        git_credentials=_OPEN_CREDENTIALS,
    )


def _push(*ref_commands: RefCommand) -> GitIntent:
    return GitIntent(
        _project="group/proj",
        operation="receive-pack",
        _method="push",
        _needs_write=True,
        _host=HOST,
        ref_commands=list(ref_commands),
    )


def _api(method: str, path: str) -> ApiIntent:
    project = "group/proj" if "/projects/" in path else ""
    return ApiIntent(_project=project, _method=method, path=path, _host=HOST)


# --- criticality gate: unit-level -----------------------------------------------


def test_criticality_gate_passes_an_empty_action_set():
    assert criticality_gate(frozenset()) is None


def test_criticality_gate_passes_actions_below_irreversible():
    actions = frozenset({Action("x.read", Criticality.READ), Action("x.write", Criticality.WRITE)})
    assert criticality_gate(actions) is None


def test_criticality_gate_denies_an_irreversible_action():
    d = criticality_gate(frozenset({Action("x.destroy", Criticality.IRREVERSIBLE)}))
    assert d is not None
    assert not d.allow and "irreversible" in d.reason
    assert "x.destroy" in d.reason


# --- criticality gate: every guard, via full_decide, regardless of config ------


def test_criticality_gate_denies_irreversible_for_git_transport_even_if_enabled():
    cfg = _cfg(actions=tuple(sorted(git_actions.by_id)))  # every id explicitly on
    intent = _push(RefCommand(SHA, ZERO, "refs/heads/claude/x"))  # branch delete
    d = git_full_decide(intent, StateView(), cfg)
    assert not d.allow and "irreversible" in d.reason


def test_criticality_gate_denies_irreversible_for_gitlab_even_if_enabled():
    cfg = _cfg(actions=tuple(sorted(git_actions.by_id)))
    intent = _api("PUT", "/projects/group%2Fproj/merge_requests/7/merge")
    d = api_full_decide(intent, StateView(), cfg)
    assert not d.allow and "irreversible" in d.reason


# --- action gate: unit-level -----------------------------------------------------


def test_action_gate_passes_when_every_recognized_action_is_enabled():
    actions = frozenset({Action("x.read", Criticality.READ)})
    assert action_gate(actions, frozenset({"x.read", "x.other"}), HOST) is None


def test_action_gate_denies_a_disabled_action_and_names_the_id():
    actions = frozenset({Action("x.read", Criticality.READ)})
    d = action_gate(actions, frozenset({"x.other"}), HOST)
    assert d is not None
    assert not d.allow and "not enabled for host" in d.reason
    assert "x.read" in d.reason


# --- action gate: every guard, via full_decide -----------------------------------


def test_action_gate_denies_a_disabled_action_for_git_transport():
    cfg = _cfg(actions=("repo.read",))
    intent = _push(RefCommand(ZERO, SHA, "refs/heads/claude/x"))  # create
    d = git_full_decide(intent, StateView(), cfg)
    assert not d.allow and "not enabled for host" in d.reason
    assert "repo.branch.create" in d.reason


def test_action_gate_allows_an_enabled_action_for_git_transport():
    cfg = _cfg(actions=("repo.read", "repo.branch.create"))
    intent = _push(RefCommand(ZERO, SHA, "refs/heads/claude/x"))
    d = git_full_decide(intent, StateView(), cfg)
    assert d.allow


def test_action_gate_denies_a_disabled_action_for_gitlab():
    intent = _api("GET", "/projects/group%2Fproj/repository/tree")
    d = api_full_decide(intent, StateView(), _cfg(), frozenset({"project.read"}))
    assert not d.allow and "not enabled for host" in d.reason
    assert "repo.read" in d.reason


def test_action_gate_allows_an_enabled_action_for_gitlab():
    intent = _api("GET", "/projects/group%2Fproj/repository/tree")
    d = api_full_decide(intent, StateView(), _cfg(), frozenset({"repo.read"}))
    assert d.allow


# --- unmatched/empty recognized: writes deny in the kernel ---------------------


def test_unmatched_write_denies_in_the_kernel_for_gitlab():
    intent = _api("DELETE", "/projects/group%2Fproj/repository/branches/claude%2Fx")
    d = api_full_decide(intent, StateView(), _cfg())
    assert not d.allow and "no recognized action" in d.reason


def test_empty_recognized_write_denies_in_the_kernel_for_git_transport():
    d = git_full_decide(_push(), StateView(), _cfg())
    assert not d.allow and "no recognized action" in d.reason


# --- order proof: a denied action performs no upstream lookup -----------------


async def test_disabled_write_action_performs_no_upstream_lookup(respx_router):
    """A host whose project.mr.comment is disabled must be denied by the
    kernel's action gate before enrich ever runs, so the MR namespace
    lookup is never made. respx_router raises on any unmocked upstream call.
    """
    enabled = tuple(a.id for a in git_actions.DEFAULT if a.id != "project.mr.comment")
    cfg = _cfg(actions=enabled)
    state = State(":memory:")
    state.mark_reconciled("git")
    state.mark_reconciled("api")
    ctx = build_context(cfg, state, AuditLog("-"))
    transport = httpx.ASGITransport(app=create_app(ctx))
    async with httpx.AsyncClient(transport=transport, base_url=f"http://{HOST}") as client:
        resp = await client.post(
            "/api/v4/projects/group%2Fproj/merge_requests/7/notes", json={"body": "hi"}
        )
    await ctx.router.aclose()
    assert resp.status_code == 403
    body = resp.json()
    assert "not enabled for host" in body["reason"]
    assert "project.mr.comment" in body["reason"]


# --- endpoint-type derivation: valid action ids come from guards' SUPPORTED ----


def test_plain_type_valid_actions_is_exactly_transport_supported():
    assert ENDPOINT_TYPES["plain"].valid_action_ids == frozenset(
        a.id for a in transport_actions.SUPPORTED
    )


def test_gitlab_type_valid_actions_is_the_union_of_both_guards_supported():
    expected = frozenset(a.id for a in transport_actions.SUPPORTED) | frozenset(
        a.id for a in gitlab_actions.SUPPORTED
    )
    assert ENDPOINT_TYPES["gitlab"].valid_action_ids == expected


def test_explicit_config_action_outside_derived_set_aborts_startup(tmp_path):
    toml = tmp_path / "warden.toml"
    toml.write_text(
        '[[git.endpoint]]\nhost = "personal-gitserver.it"\ntype = "plain"\n'
        'actions = ["repo.read", "project.mr.create"]\n'  # project.* not valid for "plain"
    )
    with pytest.raises(ConfigError, match="not valid for type"):
        from_env({}, strict=True, toml_path=str(toml))


# --- the capability layer is gone -----------------------------------------------


def test_capabilities_module_is_gone():
    with pytest.raises(ModuleNotFoundError):
        import warden.core.capabilities  # noqa: F401


def test_no_capability_gate_reference_left_in_warden():
    project_root = Path(__file__).resolve().parents[2]  # .../warden (package + tests)
    this_file = Path(__file__).resolve()
    hits = [
        str(path.relative_to(project_root))
        for path in project_root.rglob("*.py")
        if "__pycache__" not in path.parts
        and path != this_file
        and "capability_gate" in path.read_text(encoding="utf-8")
    ]
    assert hits == []
