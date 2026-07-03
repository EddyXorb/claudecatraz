"""Capability-invariant layer (§03.4, B2; docs/design/architecture-generalization,
§02-befunde.md B2, §03-guard-architektur.md §03.4, §06-migration.md Schritt 3/5).

The **vocabulary** and the compiled-in ``FORBIDDEN`` deny set are core-owned
and guard-agnostic — every guard normalises its own, already-parsed intent to
this small, closed vocabulary (what the request would *do*, independent of
how it says so: a git ref-command, a REST body field, a SQL statement, …).
:func:`core.guard.run_guarded` checks the result against :data:`FORBIDDEN`
before any guard-specific allow-logic runs (§03.2 pipeline step 6): if a
request's capabilities intersect it, the request is denied, no matter which
endpoint row or ref-check would otherwise have passed it. That is the fix for
B2 — "no tags" / "no merges" / "no branch delete" stops being a line in one
guard's own policy and becomes a property of the system every guard shares.

The intent→capability *mapping* is guard-specific and does **not** live here
(§03.3: "Kernel kennt keine GitLab-Begriffe") — see
``guards.git.policy.git_ref_capabilities`` and
``guards.gitlab_api.catalog.entries.api_capabilities``.

**Honest cost (§03.4):** this module is only as safe as the completeness of
the intent→capability mapping per guard. If a guard forgets to declare that
some call creates a tag, this layer stays silent for it — but the mapping
itself is small, pure, and golden-tested per guard, a much smaller
trust-critical surface than the scattered checks it backs up.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from .model import Decision
from .rules import R4


class Capability(str, Enum):
    """What a request would do, abstracted from the transport that says so.

    Deliberately closed — no generic extension point (§06.2 anti-goal: a
    user-declared capability field would be worthless for endpoints nobody
    has written the row for yet, and deny-invariants must never become
    configurable). Adding a member is a code change, reviewed like anything
    else on this trust boundary, not a config edit.
    """

    CREATES_REF = "creates_ref"
    DELETES_REF = "deletes_ref"
    CREATES_TAG = "creates_tag"
    MERGES = "merges"
    ESCALATES_PRIVILEGE = "escalates_privilege"
    WRITES_OUTSIDE_NAMESPACE = "writes_outside_namespace"
    DESTROYS_DATA = "destroys_data"


FORBIDDEN: frozenset[Capability] = frozenset(
    {
        Capability.DELETES_REF,
        Capability.CREATES_TAG,
        Capability.MERGES,
        Capability.ESCALATES_PRIVILEGE,
        Capability.DESTROYS_DATA,
    }
)
"""Compiled in, **never** configurable (§06.2: "Deny-Invarianten niemals
konfigurierbar machen" — not even behind a flag, not even "just for tests").

Two vocabulary members are deliberately *not* in this set:

* ``creates_ref`` — this is the agent's normal operating mode (every branch
  push and every MR the agent opens creates something). Forbidding it would
  forbid the product, not a misuse of it.
* ``writes_outside_namespace`` — unlike the other four, "the agent's
  namespace" is not a fixed fact; it is a per-deployment, operator-chosen
  setting (M2, e.g. ``Config.branch_prefixes`` for the git/GitLab guards). A
  compiled-in blanket ban on this capability would fight the namespace check
  instead of complementing it: R2 already denies it per request, keyed off
  whatever prefixes *this* deployment configured — that is the correct place
  for a value that varies by config, while ``FORBIDDEN`` is reserved for
  verbs that are unsafe under any configuration (B2's actual complaint: git
  already got this right, REST did not).

``destroys_data`` and ``escalates_privilege`` have no producer on either
shipped guard today (GitLab REST/git has no DROP-TABLE-shaped operation) —
they are already declared here for §03.7 (the Postgres guard: DDL/GRANT map
onto exactly these two) so that guard's authors extend an existing invariant
instead of negotiating a new one.
"""


def forbidden_check(caps: frozenset[Capability]) -> Optional[Decision]:
    """Deny (R4, M4 "irreversible verbs: never") if ``caps`` hits ``FORBIDDEN``.

    Returns ``None`` when clear — the same "None ⇒ still passing" shape every
    per-endpoint/per-ref check in this codebase already uses, so callers can
    slot this in ahead of them without a different calling convention. The
    kernel (:func:`core.guard.run_guarded`) runs this *before* any guard's own
    allow-logic (§03.4) — a hit here short-circuits regardless of what a
    matched endpoint row or ref-check would otherwise say.
    """
    hit = FORBIDDEN & caps
    if not hit:
        return None
    names = ", ".join(sorted(c.value for c in hit))
    return Decision(False, R4, f"forbidden capability: {names}")
