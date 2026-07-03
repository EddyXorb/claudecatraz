"""Forge/protocol guards: one subpackage per guard, each supplying Guard hooks.

* ``git`` — git Smart-HTTP: pktline, ref-policing (forge-agnostic).
* ``gitlab_api`` — GitLab's REST API guard: endpoint catalog, read-endpoint table, credential injection.
* ``gitlab`` — GitLab *forge* domain shared by both: credentials, upstream, service-account, MR-ownership.
"""

from __future__ import annotations
