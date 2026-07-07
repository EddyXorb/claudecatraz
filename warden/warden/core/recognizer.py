"""The recognizer contract: mapping a parsed request to the actions it performs.

Each guard subclasses Recognizer with its own match key as plain data;
the *pipeline* around recognizers (catalog, first-match lookup) is shared,
the *kind* of matching is guard-specific.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, Iterable, Optional, TypeVar

from .actions import Action
from .model import Intent

IntentT = TypeVar("IntentT", bound=Intent)


class Recognizer(ABC, Generic[IntentT]):
    """Answers "what would this request do" as a set of actions.

    recognize may return an empty set even when matches is true: a
    matched request whose fields carry no known meaning yields no action,
    which denies fail-closed rather than guessing.

    possible_actions is the static union of every action recognize
    could ever return for this row, independent of any concrete intent — a
    row whose action depends on a request field (e.g. state_event) lists
    every field value's action here, not just the one a given request hits.
    Nothing in the request pipeline reads it; it exists for introspection
    (the policy report).
    """

    id: str
    possible_actions: frozenset[Action]

    @abstractmethod
    def matches(self, intent: IntentT) -> bool: ...

    @abstractmethod
    def recognize(self, intent: IntentT) -> frozenset[Action]: ...


def first_match(
    catalog: Iterable[Recognizer[IntentT]], intent: IntentT
) -> Optional[Recognizer[IntentT]]:
    """Return the first recognizer in catalog whose matches is true.

    Catalog order is meaningful: put the most specific rows first.
    """
    for recognizer in catalog:
        if recognizer.matches(intent):
            return recognizer
    return None
