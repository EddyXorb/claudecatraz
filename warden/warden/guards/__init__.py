"""Forge/protocol guards: one subpackage per guard, each supplying Guard hooks.

* git — the git namespace: git Smart-HTTP transport plus git.gitlab,
  the GitLab REST guard (recognizer catalog, MR source-branch-namespace
  lookup/reconcile/quota state, and GraphQL denial).

Each guard is fully independent: neither imports the other, and the shared
collaborator both need — the httpx transport — lives in
warden.core.transport, not in a guard package.
"""

from __future__ import annotations
