"""git Smart-HTTP guard: pktline parsing, ref-policing, three git routes.

Forge-agnostic and self-contained (§07 Punkt 6): credential injection and
upstream URL shape come from the forge-neutral :mod:`warden.core.transport`;
its own branch quota state lives in :mod:`warden.guards.git.state`. This
package never imports ``guards.gitlab``/``guards.gitlab_api``.
"""

from __future__ import annotations
