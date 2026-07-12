"""git transport guard recognizers: ref-command classification and the
per-operation action gate built on top of it."""

from __future__ import annotations

import pytest

from warden.core.config import Config, GitEndpoint, HostCredentials
from warden.guards.git import actions as git_actions
from warden.guards.git.transport import policy
from warden.guards.git.transport.intent import GitIntent
from warden.guards.git.transport.pktline import RefCommand
from warden.guards.git.transport.recognizers import recognize, ref_command_action

ZERO = "0" * 40
SHA = "a" * 40
SHA2 = "b" * 40
HOST = "gitlab.example"


def _intent(operation: str, service: str = "git-upload-pack", ref_commands=()) -> GitIntent:
    return GitIntent(
        _project="group/proj",
        operation=operation,
        _method="GET" if operation == "advertise" else "POST",
        _host=HOST,
        service=service,
        ref_commands=list(ref_commands),
    )


def _cfg(actions: tuple[str, ...]) -> Config:
    return Config(
        git_endpoints=(
            GitEndpoint(
                host=HOST, type="gitlab", actions=actions, allowed_projects=("group/proj",)
            ),
        ),
        git_credentials={HOST: HostCredentials(read_token="r", write_token="w")},
    )


# --- ref_command_action: exact classification -----------------------------


@pytest.mark.parametrize(
    "old,new,ref,expected",
    [
        # heads: create / update / delete
        (ZERO, SHA, "refs/heads/claude/feature", {git_actions.REPO_BRANCH_CREATE}),
        (SHA, SHA2, "refs/heads/claude/feature", {git_actions.REPO_BRANCH_PUSH}),
        (SHA, ZERO, "refs/heads/claude/feature", {git_actions.REPO_BRANCH_DELETE}),
        # tags: only create/delete exist in the vocabulary — any non-delete
        # write (fresh tag or moving an existing one) is repo.tag.create.
        (ZERO, SHA, "refs/tags/claude/v1", {git_actions.REPO_TAG_CREATE}),
        (SHA, SHA2, "refs/tags/claude/v1", {git_actions.REPO_TAG_CREATE}),
        (SHA, ZERO, "refs/tags/claude/v1", {git_actions.REPO_TAG_DELETE}),
        # a ref outside refs/heads//refs/tags/ recognizes to nothing.
        (ZERO, SHA, "refs/notes/commits", set()),
    ],
)
def test_ref_command_action_classification(old, new, ref, expected):
    assert ref_command_action(RefCommand(old, new, ref)) == frozenset(expected)


def test_degenerate_all_zero_command_classifies_as_delete():
    # Both endpoints zero never happens over real git, but delete is checked
    # first so this doesn't misclassify as a create.
    assert ref_command_action(RefCommand(ZERO, ZERO, "refs/heads/claude/x")) == frozenset(
        {git_actions.REPO_BRANCH_DELETE}
    )


# --- whole-intent recognize(): advertise/upload-pack/receive-pack --------


@pytest.mark.parametrize("service", ["git-upload-pack", "git-receive-pack"])
def test_advertise_recognizes_repo_read_regardless_of_service(service):
    assert recognize(_intent("advertise", service=service)) == frozenset({git_actions.REPO_READ})


def test_upload_pack_recognizes_repo_read():
    assert recognize(_intent("upload-pack")) == frozenset({git_actions.REPO_READ})


def test_receive_pack_unions_actions_across_the_batch():
    intent = _intent(
        "receive-pack",
        ref_commands=[
            RefCommand(ZERO, SHA, "refs/heads/claude/new"),
            RefCommand(ZERO, SHA, "refs/tags/claude/v1"),
            RefCommand(SHA, ZERO, "refs/heads/claude/old"),
        ],
    )
    assert recognize(intent) == frozenset(
        {
            git_actions.REPO_BRANCH_CREATE,
            git_actions.REPO_TAG_CREATE,
            git_actions.REPO_BRANCH_DELETE,
        }
    )


def test_receive_pack_with_no_ref_commands_recognizes_to_nothing():
    assert recognize(_intent("receive-pack")) == frozenset()


# --- action_gate: push discovery passes under repo.read; receive-pack ------
# denies per-ref, naming the specific disabled action.


def test_discovery_passes_when_every_repo_branch_action_is_disabled():
    cfg = _cfg((git_actions.REPO_READ.id,))
    intent = _intent("advertise", service="git-receive-pack")
    assert policy.action_gate(intent, cfg) is None


def test_receive_pack_denies_per_ref_naming_the_disabled_action():
    cfg = _cfg((git_actions.REPO_READ.id,))
    intent = _intent(
        "receive-pack", ref_commands=[RefCommand(ZERO, SHA, "refs/heads/claude/feature")]
    )
    denied = policy.action_gate(intent, cfg)
    assert denied is not None
    assert not denied.allow and "not enabled for host" in denied.reason
    assert git_actions.REPO_BRANCH_CREATE.id in denied.reason


def test_receive_pack_batch_denies_on_first_bad_ref_action():
    # repo.branch.create enabled, repo.branch.push is not: the second
    # ref-command (an update) is what fails the batch.
    cfg = _cfg((git_actions.REPO_READ.id, git_actions.REPO_BRANCH_CREATE.id))
    intent = _intent(
        "receive-pack",
        ref_commands=[
            RefCommand(ZERO, SHA, "refs/heads/claude/new"),
            RefCommand(SHA, SHA2, "refs/heads/claude/existing"),
        ],
    )
    denied = policy.action_gate(intent, cfg)
    assert denied is not None
    assert not denied.allow and "not enabled for host" in denied.reason
    assert git_actions.REPO_BRANCH_PUSH.id in denied.reason


# --- criticality wins even when an operator enables every action ----------


def test_tag_and_delete_denied_by_criticality_even_with_every_action_enabled():
    cfg = _cfg(tuple(sorted(git_actions.by_id)))
    tag = policy.action_gate(
        _intent("receive-pack", ref_commands=[RefCommand(ZERO, SHA, "refs/tags/claude/v1")]), cfg
    )
    delete = policy.action_gate(
        _intent("receive-pack", ref_commands=[RefCommand(SHA, ZERO, "refs/heads/claude/feature")]),
        cfg,
    )
    assert tag is not None and not tag.allow and "irreversible" in tag.reason
    assert delete is not None and not delete.allow and "irreversible" in delete.reason
