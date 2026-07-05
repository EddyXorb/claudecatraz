"""GitLab REST guard: recognizer catalog, MR source-branch-namespace
lookup/reconcile, credential injection. Also serves ``/api/graphql*`` —
denied unconditionally, an unmodelled channel.
"""

from __future__ import annotations
