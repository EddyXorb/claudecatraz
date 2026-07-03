"""Forge/protocol guards: one subpackage per guard, each supplying Guard hooks.

* ``git`` — git Smart-HTTP: pktline, ref-policing, own branch-quota state
  (forge-agnostic; depends only on :mod:`warden.core.transport`).
* ``gitlab_api`` — GitLab's REST API guard: endpoint catalog, read-endpoint
  table, credential injection, MR ownership/reconcile/quota state.

Each guard is fully independent (§07 Punkt 6): neither imports the other, and
the shared collaborator both need — the httpx transport — lives in
:mod:`warden.core.transport`, not in a guard package.
"""

from __future__ import annotations
