"""Central rule registry: one place for rule ids, preventing string literal drift.

Every warden.core.model.Decision carries a bare rule id ("R0".."R6")
for the audit log. Callers import R0..R6 instead of literals,
and rule resolves a bare id to its definition (meta-rule + summary),
so typos fail loudly instead of silently reaching the audit log.

**Kernel namespace (prepared, not yet active).** Reserve core.* namespace
for kernel-enforced decisions once a second guard makes bare ids ambiguous.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final, Mapping


class MetaRule(str, Enum):
    """Resource-agnostic meta-rules that R0..R6 instantiate.

    Partitioning concrete rule ids by meta-rule prevents different concepts
    from sharing the same id (branch namespace vs. "never" capabilities).
    """

    M0 = "M0"  # Mode gate: off | read-only | read-write, per resource.
    M1 = "M1"  # Reads with a least-privilege read credential.
    M2 = "M2"  # Writes only in the agent's own namespace.
    M3 = "M3"  # Actions only on objects the agent itself authored.
    M4 = "M4"  # Irreversible / privilege-escalating capabilities: never.
    M5 = "M5"  # Quotas + rate limits, fail-safe on unresolved state.
    M6 = "M6"  # Credential/network isolation + resource allowlist.
    MA = "MA"  # Full audit with a rule id on every decision — cross-cutting.


@dataclass(frozen=True)
class RuleDef:
    """One rule id: its meta-rule and a human summary (for docs/log tooling)."""

    id: str
    meta: MetaRule
    summary: str


# --- rule ids (Decision.rule sources these instead of bare literals) -----------

R0: Final = "R0"
R1: Final = "R1"
R2: Final = "R2"
R3: Final = "R3"
R4: Final = "R4"
R5: Final = "R5"
R6: Final = "R6"

_DEFS: tuple[RuleDef, ...] = (
    RuleDef(R0, MetaRule.M0, "Mode gate — git forge disabled or writes disabled"),
    RuleDef(R1, MetaRule.M1, "Read pass-through with the least-privilege read token"),
    RuleDef(R2, MetaRule.M2, "Write limited to the agent's own branch namespace"),
    RuleDef(R3, MetaRule.M3, "Write limited to objects the agent itself authored"),
    RuleDef(
        R4,
        MetaRule.M4,
        "Irreversible verb, never permitted: merge, tag push, branch delete",
    ),
    RuleDef(R5, MetaRule.M5, "Quota / rate limit, fail-safe while state is unresolved"),
    RuleDef(R6, MetaRule.M6, "Resource allowlist boundary (project/credential scope)"),
)

RULES: Mapping[str, RuleDef] = {d.id: d for d in _DEFS}


def rule(rule_id: str) -> RuleDef:
    """Resolve a bare rule id to its RuleDef.

    Raises KeyError for an id outside the registry — every
    Decision.rule must trace back to a defined rule; an unregistered id
    reaching this function is a bug in the caller, not a value to tolerate.
    """
    return RULES[rule_id]


# --- kernel namespace (prepared, not yet emitted) -----

KERNEL_NAMESPACE: Final = "core"
GITLAB_NAMESPACE: Final = "gitlab"


def qualify(rule_id: str, *, namespace: str = GITLAB_NAMESPACE) -> str:
    """Build a namespaced rule id ("gitlab.R4", "core.R5").

    Not used for logging yet (module docstring) — a helper the guard-rename
    step can call once a second guard makes bare ids ambiguous. Validates
    rule_id against the registry so a namespaced id can never reference a
    rule that does not exist.
    """
    rule(rule_id)  # raises KeyError on an unregistered id
    if not namespace:
        raise ValueError("namespace must be non-empty")
    return f"{namespace}.{rule_id}"
