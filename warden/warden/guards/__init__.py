"""Forge/protocol guards (§03.3/03.5; docs/design/architecture-generalization,
§03-guard-architektur.md §03.3): one subpackage per guard, each supplying the
:class:`~warden.core.guard.Guard` hooks the kernel drives.

* ``git`` — git Smart-HTTP: pktline, ref-policing. Forge-agnostic (§03.3: no
  GitLab-specific concept appears here).
* ``gitlab_api`` — GitLab's REST API guard: the endpoint catalog, read-endpoint
  table, REST credential injection.
* ``gitlab`` — the GitLab *forge* domain shared by both guards above:
  credentials, upstream transport, service-account, MR-ownership, reconcile
  (:mod:`warden.guards.gitlab.forge`). Not a guard itself.
"""

from __future__ import annotations
