"""The recognizer contract: mapping a parsed request to the actions it performs.

Each guard subclasses Recognizer with its own match key as plain data; the
pipeline around recognizers is shared, the kind of matching is guard-specific.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, Iterable, Optional, TypeVar

from .actions import Action
from .model import Intent

IntentT = TypeVar("IntentT", bound=Intent)


class Recognizer(ABC, Generic[IntentT]):
    """Answers "what would this request do" as a set of actions, or that it
    does not apply to this request at all.

    Returns None when it does not apply (caller tries the next one), or the
    possibly-empty recognized action set when it does — an empty set still
    denies fail-closed, it is not the same as "try the next recognizer".
    possible_actions is the static union of every action this row could
    ever return, for introspection only.
    """

    id: str
    possible_actions: frozenset[Action]

    @abstractmethod
    def __call__(self, intent: IntentT) -> Optional[frozenset[Action]]: ...


def first_recognized(
    catalog: Iterable[Recognizer[IntentT]], intent: IntentT
) -> Optional[frozenset[Action]]:
    """The first non-None result from calling each recognizer in catalog in
    order, or None if every one of them declined the intent.

    Catalog order is meaningful: put the most specific rows first.
    """
    for recognizer in catalog:
        result = recognizer(intent)
        if result is not None:
            return result
    return None
