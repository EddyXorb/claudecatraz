"""The GitLab *forge* domain (§03.3/§03.5): credentials, upstream transport,
service-account resolution, MR-ownership and reconcile — the GitLab-server
logic shared by BOTH the git-transport guard (``guards.git``) and the REST-API
guard (``guards.gitlab_api``). Distinct from ``guards.gitlab_api``, which is
the REST guard itself (the endpoint catalog, read-endpoint table, REST
credential injection) — this package is what the REST guard borrows *from*,
not the guard.
"""

from __future__ import annotations
