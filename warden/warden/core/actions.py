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

    id and quota_kind are opaque strings to core: never parsed or
    interpreted, just stored. quota_kind is None for reads and denies.
    """

    id: str
    criticality: Criticality
    quota_kind: Optional[str] = None
