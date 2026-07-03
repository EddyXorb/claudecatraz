"""Check registry (§04.1, F2/F10; docs/design/architecture-generalization,
§04-policy-erweiterbarkeit.md §04.1).

Before this module the predicates were free functions in ``api_endpoints.py``:
``field_has_prefix`` was already parametrised but not registered under a
stable name; ``mr_owned_by_claude`` and ``not_merge_intent`` were one-off,
unparametrised functions duplicating what a generalised check could express
(F10: ``src_branch_prefix``/``ref_prefix`` were the same function twice before
an earlier step already folded them into ``field_has_prefix`` — this module
is where that check becomes a *named* registry entry instead of just a local
factory).

Every check returns ``None`` when it passes (the same "None ⇒ still passing"
shape ``policy._decide_api`` already relies on), or a deny :class:`Decision`.
"""

from __future__ import annotations

from typing import Callable, Mapping, Optional

from ..config import Config
from ..model import Decision, ProxyRequest, StateView
from ..rules import R2, R3, R4
from .model import RegisteredCheck


def field_has_prefix(field: str) -> RegisteredCheck:
    """Factory: deny unless ``req.fields[field]`` is in the branch namespace (R2).

    Checks against :meth:`Config.in_branch_namespace` — the single source of
    truth for the ``branch_prefixes`` union — never a literal prefix baked
    into the check itself, so a deployment-wide namespace change takes effect
    for every catalog entry using this check without a catalog edit.
    """

    def check(req: ProxyRequest, state: StateView, cfg: Config) -> Optional[Decision]:
        value = req.fields.get(field, "")
        if cfg.in_branch_namespace(value):
            return None
        return Decision(
            False, R2, f"{field} {value!r} outside allowed prefixes {cfg.branch_prefixes!r}"
        )

    return RegisteredCheck(name=f"field_has_prefix({field!r})", fn=check)


def _owned_by_agent(req: ProxyRequest, state: StateView, cfg: Config) -> Optional[Decision]:
    if req.mr_owner_ok is True:
        return None
    if req.mr_owner_ok is None:
        return Decision(False, R3, "MR ownership could not be verified")
    return Decision(False, R3, "MR not owned by the service account")


# A singleton, not a factory: unparametrised and reused by every ownership-
# gated catalog entry. ``needs={"mr_owner"}`` is the F2 fix — api_proxy asks
# "does any check on the matched entry need mr_owner?" instead of comparing
# this object's identity against a hardcoded predicate.
OWNED_BY_AGENT = RegisteredCheck(
    name="owned_by_agent", fn=_owned_by_agent, needs=frozenset({"mr_owner"})
)


def field_not_equals(field: str, value: object) -> RegisteredCheck:
    """Factory: deny when ``req.fields[field] == value`` (generalises the
    former ``not_merge_intent``, which was this check hardcoded to
    ``state_event == "merge"``)."""

    def check(req: ProxyRequest, state: StateView, cfg: Config) -> Optional[Decision]:
        if req.fields.get(field) == value:
            return Decision(False, R4, f"{field}={value!r} is not permitted on this endpoint")
        return None

    return RegisteredCheck(name=f"field_not_equals({field!r}, {value!r})", fn=check)


# The registry itself (§04.1): named, parametrisable check factories a
# catalog entry can reference by name. Not consulted by the runtime path
# today (catalog entries build ``RegisteredCheck`` values directly by calling
# these factories) — it exists as the single, stable list of what a catalog
# PR is allowed to build a row's checks out of, and as the anchor for any
# future config-facing introspection (``catraz doctor``, §04.3).
CHECKS: Mapping[str, Callable[..., RegisteredCheck]] = {
    "field_has_prefix": field_has_prefix,
    "owned_by_agent": lambda: OWNED_BY_AGENT,
    "field_not_equals": field_not_equals,
}
