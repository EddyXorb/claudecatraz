"""The action model's types: criticality and the action itself.

Core owns these types only, never a vocabulary — no concrete action id
appears here. Each guard namespace defines its own actions using these types.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Optional


class Criticality(IntEnum):
    """How hard an action is to undo, ordered from harmless to compiled-in deny."""

    READ = 0
    WRITE = 1
    IRREVERSIBLE = 2


@dataclass(frozen=True)
class Action:
    """One named effect a request can have, with its criticality.

    The id is an opaque string to every consumer in core: never parsed, no
    grammar enforced. quota_kind is the same — an opaque tag a namespace
    attaches so its guard knows which quota bucket this action spends (None
    for reads and for the never-permitted irreversible actions); core stores
    it and never interprets it. Frozen and hashable so actions can live in sets.
    """

    id: str
    criticality: Criticality
    quota_kind: Optional[str] = None
