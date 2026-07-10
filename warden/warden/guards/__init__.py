"""Forge/protocol guards: one subpackage per guard, each supplying Guard hooks.

Guards are fully independent — none imports another — and share the httpx
transport from warden.core.transport rather than each other.
"""

from __future__ import annotations
