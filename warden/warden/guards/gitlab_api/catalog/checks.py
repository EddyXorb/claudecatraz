"""Check registry: named, parametrised check factories catalog entries reference.

Every check returns ``None`` when it passes (the "None ⇒ still passing" shape),
or a deny :class:`~warden.core.model.Decision`.
"""

from __future__ import annotations

from typing import Callable, Mapping, Optional

from ....core.config import Config
from ....core.model import Decision, StateView
from ....core.rules import R2, R3, R4
from ..intent import ApiIntent
from .model import RegisteredCheck


def field_has_prefix(field: str) -> RegisteredCheck:
    """Factory: deny unless ``intent.fields[field]`` is in the branch namespace (R2).

    Checks against :meth:`~warden.core.config.Config.in_branch_namespace` —
    the single source of truth for the ``branch_prefixes`` union — never a
    literal prefix baked into the check itself, so a deployment-wide
    namespace change takes effect for every catalog entry using this check
    without a catalog edit.
    """

    def check(intent: ApiIntent, state: StateView, cfg: Config) -> Optional[Decision]:
        value = intent.fields.get(field, "")
        if cfg.in_branch_namespace(value):
            return None
        return Decision(
            False, R2, f"{field} {value!r} outside allowed prefixes {cfg.branch_prefixes!r}"
        )

    return RegisteredCheck(name=f"field_has_prefix({field!r})", fn=check)


def _owned_by_agent(intent: ApiIntent, state: StateView, cfg: Config) -> Optional[Decision]:
    if intent.mr_owner_ok is True:
        return None
    if intent.mr_owner_ok is None:
        return Decision(False, R3, "MR ownership could not be verified")
    return Decision(False, R3, "MR not owned by the service account")


# A singleton, not a factory: unparametrised and reused by every ownership-gated entry.
# ``needs={"mr_owner"}`` allows the guard to ask "does any check need mr_owner?" instead
# of comparing object identity against a hardcoded predicate.
OWNED_BY_AGENT = RegisteredCheck(
    name="owned_by_agent", fn=_owned_by_agent, needs=frozenset({"mr_owner"})
)


def field_not_equals(field: str, value: object) -> RegisteredCheck:
    """Factory: deny when ``intent.fields[field] == value`` (generalises the
    former ``not_merge_intent``, which was this check hardcoded to
    ``state_event == "merge"``)."""

    def check(intent: ApiIntent, state: StateView, cfg: Config) -> Optional[Decision]:
        if intent.fields.get(field) == value:
            return Decision(False, R4, f"{field}={value!r} is not permitted on this endpoint")
        return None

    return RegisteredCheck(name=f"field_not_equals({field!r}, {value!r})", fn=check)


# The registry: named, parametrisable check factories. The stable list of what
# a catalog PR can use to build a row's checks, and anchor for future
# config-facing introspection.
CHECKS: Mapping[str, Callable[..., RegisteredCheck]] = {
    "field_has_prefix": field_has_prefix,
    "owned_by_agent": lambda: OWNED_BY_AGENT,
    "field_not_equals": field_not_equals,
}
