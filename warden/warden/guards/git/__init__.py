"""git Smart-HTTP guard: pktline parsing, ref-policing, three git routes.

Forge-agnostic: no GitLab vocabulary here; credential injection and upstream URL shape
come from :mod:`warden.guards.gitlab` (shared forge domain).
"""

from __future__ import annotations
