"""Capability-invariant layer (§03.4, B2; docs/design/architecture-generalization,
§02-befunde.md B2, §03-guard-architektur.md §03.4, §06-migration.md Schritt 3).

Every request — on either channel — is normalised to a small, **closed**
vocabulary of what it would *do* if allowed, independent of how it says so
(a git ref-command, a REST body field, …). One compiled-in ``FORBIDDEN`` set
is checked before any allow rule: if a request's capabilities intersect it,
the request is denied, no matter which endpoint row or ref-check would
otherwise have passed it. That is the fix for B2 — "no tags" / "no merges" /
"no branch delete" stops being a line in ``policy.check_ref`` (git-only) and
becomes a property of the system that both channels share.

**Honest cost (§03.4):** this module is only as safe as the completeness of
the intent→capability mapping per channel. If a guard forgets to declare that
some call creates a tag, this layer stays silent for it — but the mapping
itself is small, pure, and golden-tested (``tests/test_capabilities.py``), a
much smaller trust-critical surface than the scattered checks it backs up.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from .config import Config
from .model import Decision
from .pktline import RefCommand
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
  namespace" is not a fixed fact; it is ``Config.branch_prefixes``, an
  operator-chosen, per-deployment setting (M2). A compiled-in blanket ban on
  this capability would fight the namespace check instead of complementing
  it: R2 (``policy.check_ref``, ``api_endpoints.field_has_prefix``) already
  denies it per request, keyed off whatever prefixes *this* deployment
  configured — that is the correct place for a value that varies by config,
  while ``FORBIDDEN`` is reserved for verbs that are unsafe under any
  configuration (B2's actual complaint: git already got this right, REST
  did not).

``destroys_data`` and ``escalates_privilege`` have no producer on either
guard today (GitLab REST/git has no DROP-TABLE-shaped operation) — they are
already declared here for §03.7 (the Postgres guard: DDL/GRANT map onto
exactly these two) so that guard's authors extend an existing invariant
instead of negotiating a new one.
"""


def forbidden_check(caps: frozenset[Capability]) -> Optional[Decision]:
    """Deny (R4, M4 "irreversible verbs: never") if ``caps`` hits ``FORBIDDEN``.

    Returns ``None`` when clear — same "None ⇒ still passing" shape as the
    per-endpoint checks in ``api_endpoints.py``, so callers can slot this in
    ahead of them without a different calling convention. Callers run this
    *before* any endpoint/ref allow-logic (§03.4) — a hit here short-circuits
    regardless of what a matched endpoint row or ref-check would otherwise say.
    """
    hit = FORBIDDEN & caps
    if not hit:
        return None
    names = ", ".join(sorted(c.value for c in hit))
    return Decision(False, R4, f"forbidden capability: {names}")


def git_ref_capabilities(cmd: RefCommand, cfg: Config) -> frozenset[Capability]:
    """Map one git ref-command to capabilities — trivial and exact (§03.4).

    Mirrors, but does not replace, the special cases in ``policy.check_ref``
    (kept as defense-in-depth, A10): this mapping alone must be enough to
    trigger :func:`forbidden_check`, independent of ``check_ref``'s own logic.

    * A delete (``new`` is all-zero) is ``deletes_ref`` regardless of ref
      type — a tag delete is a delete, not additionally a tag *creation*.
    * A non-deleting push to ``refs/tags/*`` is ``creates_tag``.
    * Anything else is a branch write: ``creates_ref`` when it creates a new
      branch, plus ``writes_outside_namespace`` when the (heads-prefix
      stripped) ref name is outside ``cfg.branch_prefixes`` (M2) — not
      forbidden by itself (see :data:`FORBIDDEN`'s docstring), but part of
      the shared vocabulary so a future consumer (e.g. an audit report) can
      ask "did this write leave the namespace" without re-deriving it.
    """
    if cmd.is_delete:
        return frozenset({Capability.DELETES_REF})
    if cmd.ref.startswith("refs/tags/"):
        return frozenset({Capability.CREATES_TAG})
    ref = cmd.ref.removeprefix("refs/heads/")
    caps: set[Capability] = {Capability.CREATES_REF} if cmd.is_create else set()
    if not cfg.in_branch_namespace(ref):
        caps.add(Capability.WRITES_OUTSIDE_NAMESPACE)
    return frozenset(caps)
