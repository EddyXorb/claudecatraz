"""Map a git Smart-HTTP intent to the actions it performs.

advertise (either service) and upload-pack never write a ref, so both
recognize to repo.read — push discovery carries the write token but only
reads refs; the actual write is receive-pack's pack payload. receive-pack
recognizes each ref-command independently and unions the result over the
batch, so one push can require several actions at once.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from ....core.actions import Action
from ....core.recognizer import Recognizer, first_recognized
from .. import actions as git_actions
from .intent import GitIntent
from .pktline import RefCommand


def ref_command_action(cmd: RefCommand) -> frozenset[Action]:
    """Classify one receive-pack ref-command.

    Delete is checked before create so a degenerate all-zero-to-all-zero
    command (never sent by real git, but not excluded by the wire format)
    recognizes as a delete rather than a create. A ref outside
    refs/heads/ / refs/tags/ recognizes to nothing — fail-closed.
    """
    if cmd.ref.startswith("refs/heads/"):
        if cmd.is_delete:
            return frozenset({git_actions.REPO_BRANCH_DELETE})
        if cmd.is_create:
            return frozenset({git_actions.REPO_BRANCH_CREATE})
        return frozenset({git_actions.REPO_BRANCH_PUSH})
    if cmd.ref.startswith("refs/tags/"):
        if cmd.is_delete:
            return frozenset({git_actions.REPO_TAG_DELETE})
        return frozenset({git_actions.REPO_TAG_CREATE})
    return frozenset()


def _read_actions(intent: GitIntent) -> frozenset[Action]:
    return frozenset({git_actions.REPO_READ})


def _receive_pack_actions(intent: GitIntent) -> frozenset[Action]:
    actions: frozenset[Action] = frozenset()
    for cmd in intent.ref_commands:
        actions |= ref_command_action(cmd)
    return actions


@dataclass(frozen=True)
class GitRecognizer(Recognizer[GitIntent]):
    """One row: which operations it matches, and how it recognizes them."""

    id: str
    operations: frozenset[str]
    recognize_fn: Callable[[GitIntent], frozenset[Action]]
    possible_actions: frozenset[Action]

    def __call__(self, intent: GitIntent) -> Optional[frozenset[Action]]:
        return self.recognize_fn(intent) if intent.operation in self.operations else None


CATALOG: tuple[GitRecognizer, ...] = (
    GitRecognizer(
        "git.read",
        frozenset({"advertise", "upload-pack"}),
        _read_actions,
        frozenset({git_actions.REPO_READ}),
    ),
    GitRecognizer(
        "git.receive_pack",
        frozenset({"receive-pack"}),
        _receive_pack_actions,
        frozenset(
            {
                git_actions.REPO_BRANCH_CREATE,
                git_actions.REPO_BRANCH_PUSH,
                git_actions.REPO_BRANCH_DELETE,
                git_actions.REPO_TAG_CREATE,
                git_actions.REPO_TAG_DELETE,
            }
        ),
    ),
)


def recognize(intent: GitIntent) -> frozenset[Action]:
    """Whole-intent action set via CATALOG. Empty when unmatched."""
    return first_recognized(CATALOG, intent) or frozenset()
