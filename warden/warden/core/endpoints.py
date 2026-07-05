"""Endpoint type: a configured host's declared composition of guards.

Guards are named, not imported — core must never depend on ``warden.guards.*``.
Resolving a name to a guard instance is an assembly-time concern.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EndpointType:
    """A toml ``type`` value and the guard names composing it."""

    name: str
    guards: tuple[str, ...]
