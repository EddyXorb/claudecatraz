"""Forge/protocol guards (§03.3; docs/design/architecture-generalization,
§03-guard-architektur.md §03.3): one subpackage per guard, each supplying the
:class:`~warden.core.guard.Guard` hooks the kernel drives.

* ``git`` — git Smart-HTTP: pktline, ref-policing. Forge-agnostic (§03.3: no
  GitLab-specific concept appears here).
* ``gitlab_api`` — GitLab's REST API: the endpoint catalog, read-endpoint
  table, ownership/reconcile, credential injection.
"""

from __future__ import annotations
