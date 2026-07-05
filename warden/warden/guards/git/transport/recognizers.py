"""Map a git Smart-HTTP intent to the actions it performs.

advertise (either service) and upload-pack never write a ref, so both
recognize to ``repo.read`` — push discovery carries the write token but only
reads refs; the actual write is receive-pack's pack payload. receive-pack
recognizes each ref-command independently and unions the result over the
batch, so one push can require several actions at once.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ....core.actions import Action
from ....core.recognizer import Recognizer, first_match
from .. import actions as git_actions
from .intent import GitIntent
from .pktline import RefCommand


def ref_command_action(cmd: RefCommand) -> frozenset[Action]:
    """Classify one receive-pack ref-command.

    Delete is checked before create so a degenerate all-zero-to-all-zero
    command (never sent by real git, but not excluded by the wire format)
    recognizes as a delete rather than a create. A ref outside
    ``refs/heads/``/``refs/tags/`` recognizes to nothing — fail-closed.
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

    def matches(self, intent: GitIntent) -> bool:
        return intent.operation in self.operations

    def recognize(self, intent: GitIntent) -> frozenset[Action]:
        return self.recognize_fn(intent)


CATALOG: tuple[GitRecognizer, ...] = (
    GitRecognizer("git.read", frozenset({"advertise", "upload-pack"}), _read_actions),
    GitRecognizer("git.receive_pack", frozenset({"receive-pack"}), _receive_pack_actions),
)


def recognize(intent: GitIntent) -> frozenset[Action]:
    """Whole-intent action set via ``CATALOG``. Empty when unmatched."""
    row = first_match(CATALOG, intent)
    return row.recognize(intent) if row is not None else frozenset()
