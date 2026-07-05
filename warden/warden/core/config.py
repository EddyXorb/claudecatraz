"""The typed, frozen Config value the policy consumes.

Only the *model* half of the config layer lives here — building a
:class:`Config` from env + ``warden.toml`` (secret files, precedence, hard
fail-closed validation) is :mod:`warden.core.config_load`'s job. Split kept so
neither half outgrows a readable file and the many ``Config`` importers
(guards, catalog, tests) depend on the small value type, not on the loading
machinery.

There is no global "mode": access is derived per host from which of that
host's tokens are present (:meth:`Config.access_mode`) — a deployment with
no configured/open endpoints denies everything by simple absence, not by a
declared ``off``.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import Literal, Mapping, Optional, TypeVar

_T = TypeVar("_T")


class ConfigError(RuntimeError):
    """Raised on invalid/missing configuration — the Warden refuses to start."""


@dataclass(frozen=True)
class HostCredentials:
    """One host's resolved read/write tokens, keyed by normalised host in
    ``Config.git_credentials``. Resolved from the grouped ``read_tokens``/
    ``write_tokens`` secret files — a host with no entry (or a missing read
    token) is simply ``closed`` (:meth:`Config.access_mode`), never a crash."""

    read_token: str = ""
    write_token: str = ""


AccessMode = Literal["closed", "read-only", "read-write"]


def normalize_project(project: str) -> str:
    """Canonical project path: drop the git ``.git`` suffix and surrounding slashes.

    The git Smart-HTTP path carries ``group/proj.git``; the allowlist and REST
    forms use the bare ``group/proj``. Normalising in one place keeps allowlist
    checks, REST project-ids, upstream URLs and state keys consistent (one
    definition), so a pushed branch is not counted twice in ``agent_branches``."""
    return project.removesuffix(".git").strip("/")


def _cascade(override: Optional[_T], domain: Optional[_T], builtin: _T) -> _T:
    """First set value in the override -> domain-default -> built-in-default chain."""
    if override is not None:
        return override
    if domain is not None:
        return domain
    return builtin


_DEFAULT_BRANCH_PREFIXES: tuple[str, ...] = ("claude/",)
_DEFAULT_MAX_OPEN_MRS = 5
_DEFAULT_MAX_OPEN_BRANCHES = 10
_DEFAULT_MAX_WRITES_PER_HOUR = 60
_DEFAULT_MAX_PUSH_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True)
class GitRules:
    """Overridable git policy knobs. ``None`` means "not set here" so a cascade
    (endpoint override -> domain default -> built-in default) can tell that
    apart from an explicit, deliberately narrow value."""

    branch_prefixes: Optional[tuple[str, ...]] = None
    max_open_branches: Optional[int] = None
    max_open_mrs: Optional[int] = None
    max_writes_per_hour: Optional[int] = None
    max_push_bytes: Optional[int] = None


@dataclass(frozen=True)
class GitEndpoint:
    """One git host: its identity/scope (``host``, ``type``, ``allowed_projects``)
    plus optional rule/action overrides. ``allowed_projects`` is always
    per-endpoint — a project path is only unambiguous relative to the host it
    lives on.

    ``actions`` follows the same cascade contract as ``rules``:
    ``None`` means "no override, the domain default (``Config.git_actions``)
    or built-in default applies"; an explicit ``()`` means "this endpoint may
    do nothing" — a deliberately narrow value, never to be normalised into
    ``None`` (the two must stay distinguishable)."""

    host: str
    type: str
    allowed_projects: tuple[str, ...] = ()
    rules: GitRules = field(default_factory=GitRules)
    actions: Optional[tuple[str, ...]] = None

    def project_allowed(self, project: str) -> bool:
        """Default-deny match against this endpoint's own ``allowed_projects``."""
        project = normalize_project(project)
        return any(project == allowed.strip("/") for allowed in self.allowed_projects)


@dataclass(frozen=True)
class Config:
    branch_prefixes: tuple[str, ...] = ("claude/",)
    max_open_mrs: int = 5
    max_open_branches: int = 10
    max_writes_per_hour: int = 60
    # Cheap, no packfile-parsing push-size cap: checked against the
    # receive-pack request's Content-Length before the body is streamed
    # upstream. Generous default so a normal push is never affected.
    max_push_bytes: int = 50 * 1024 * 1024
    allowed_projects: tuple[str, ...] = ()
    reconcile_interval_s: int = 300
    state_db_path: str = "/var/lib/warden/state.db"
    audit_log_path: str = "/var/log/warden/audit.jsonl"
    log_path: str = "/var/log/warden/warden.log"
    agent_port: int = 8080
    admin_port: int = 9090
    admin_host: str = "0.0.0.0"
    # [git.rules] domain defaults and [[git.endpoint]] entries (one host each) —
    # every routable host is an explicit `GitEndpoint`, wired into
    # `UpstreamRouter`/`host_gate` (core.transport, core.guard). Empty
    # git_endpoints ⇒ no endpoints configured ⇒ every host is denied (real
    # default-deny, not "feature off").
    git_rules: GitRules = field(default_factory=GitRules)
    # [git].actions domain default. None ⇒ the key is absent from
    # warden.toml, meaning the built-in default (guards.git.actions.DEFAULT)
    # applies — same "absent != empty" contract as git_rules' individual
    # fields, kept at the whole-list granularity since actions replace
    # completely rather than merging per-key.
    git_actions: Optional[tuple[str, ...]] = None
    git_endpoints: tuple[GitEndpoint, ...] = ()
    # Per-endpoint tokens resolved from the grouped read_tokens/write_tokens
    # files, keyed by normalised host. Backs access_mode() and
    # UpstreamRouter's per-endpoint credentials.
    git_credentials: Mapping[str, HostCredentials] = field(default_factory=dict)

    def project_allowed(self, project: str) -> bool:
        """Default-deny match against ``ALLOWED_PROJECTS``, path form only.

        No prefix/subpath match — the allowlist names concrete projects, never
        group prefixes. GitLab also accepts a project's
        numeric id interchangeably with its path; matching that form is a
        REST-API-guard concept (the id is only known after reconcile talks to
        GitLab) — see ``guards.gitlab_api.guard.ApiGuard.project_allowed``,
        not this method.
        """
        project = normalize_project(project)
        return any(project == allowed.strip("/") for allowed in self.allowed_projects)

    def in_branch_namespace(self, name: str) -> bool:
        """True iff ``name`` starts with any configured branch prefix.

        Single source of truth for the branch namespace: git guard's R2/R3 checks
        and reconcile filters call this instead of comparing directly.
        """
        return any(name.startswith(prefix) for prefix in self.branch_prefixes)

    @staticmethod
    def normalize_host(host: str) -> str:
        """Case/port/trailing-dot-insensitive host normalisation.

        The single definition shared by :meth:`host_allowed`,
        :class:`~warden.core.transport.UpstreamRouter`'s header lookup and
        every ``(host, project)`` state key — so the same raw ``Host`` header
        always maps to the same normalised key everywhere it is used.
        """
        return host.split(":", 1)[0].strip().lower().rstrip(".")

    def host_allowed(self, host: str) -> bool:
        """Host-header gate, wired into the kernel path via
        ``core.guard.host_gate``. Real default-deny: a host passes
        only if it has a configured ``[[git.endpoint]]`` entry *and* that
        endpoint currently resolves to a usable read credential
        (:meth:`access_mode` is not ``"closed"``). An empty endpoint list
        (or an entirely unlisted host, or a listed-but-tokenless one) is
        denied — there is no "empty allowlist ⇒ allow everything" fallback.
        """
        normalized = self.normalize_host(host)
        return (
            bool(normalized)
            and normalized in self.git_allowed_hosts
            and self.access_mode(normalized) != "closed"
        )

    def resolve_target_host(self, header: str) -> Optional[str]:
        """The canonical host key for state/reconcile/Upstream lookup, given
        a raw incoming ``Host`` header.

        The normalised header if it names a configured endpoint, else
        ``None`` (unknown host — the caller must deny, never fabricate a key
        for it; ``core.guard.host_gate`` already denies this case earlier in
        the pipeline, so callers past that point should never actually
        observe ``None``).
        """
        normalized = self.normalize_host(header)
        return normalized if normalized in self.git_allowed_hosts else None

    @functools.cached_property
    def _endpoints_by_host(self) -> Mapping[str, GitEndpoint]:
        """Normalised-host -> endpoint lookup, built once from ``git_endpoints``."""
        return {self.normalize_host(e.host): e for e in self.git_endpoints}

    def endpoint_for(self, host: str) -> Optional[GitEndpoint]:
        """The configured endpoint for this host, or ``None`` if none matches."""
        return self._endpoints_by_host.get(self.normalize_host(host))

    @functools.cached_property
    def git_allowed_hosts(self) -> frozenset[str]:
        """Normalised hosts with a configured ``[[git.endpoint]]`` entry."""
        return frozenset(self._endpoints_by_host)

    @property
    def effective_hosts(self) -> tuple[str, ...]:
        """Host list: every configured endpoint's normalised host, in
        ``git_endpoints`` order. Empty when no endpoint is configured. Includes
        ``closed`` endpoints; see :attr:`open_hosts` for the per-endpoint
        reconcile's trimmed variant."""
        return tuple(self.normalize_host(e.host) for e in self.git_endpoints)

    @property
    def open_hosts(self) -> tuple[str, ...]:
        """:attr:`effective_hosts`, trimmed to endpoints that currently have a
        usable read credential. The single definition shared by
        :func:`~warden.guards.git.reconcile.reconcile_branches`
        and :func:`~warden.guards.gitlab_api.reconcile.reconcile_mrs` — a
        ``closed`` endpoint is unreachable via ``host_gate`` anyway (R6) and
        never needs reconciling (see :func:`~warden.core.transport.for_each_host_project`'s
        docstring for why passing a closed host through would be a bug, not a
        tolerated case)."""
        return tuple(
            self.normalize_host(e.host)
            for e in self.git_endpoints
            if self.access_mode(e.host) != "closed"
        )

    def effective_rules(self, host: str) -> GitRules:
        """Per-key cascade for ``host``: endpoint override, else ``git_rules``
        domain default, else built-in default. A list override replaces the
        domain list wholesale rather than merging with it."""
        endpoint = self.endpoint_for(host)
        override = endpoint.rules if endpoint is not None else GitRules()
        domain = self.git_rules
        return GitRules(
            branch_prefixes=_cascade(
                override.branch_prefixes, domain.branch_prefixes, _DEFAULT_BRANCH_PREFIXES
            ),
            max_open_branches=_cascade(
                override.max_open_branches, domain.max_open_branches, _DEFAULT_MAX_OPEN_BRANCHES
            ),
            max_open_mrs=_cascade(
                override.max_open_mrs, domain.max_open_mrs, _DEFAULT_MAX_OPEN_MRS
            ),
            max_writes_per_hour=_cascade(
                override.max_writes_per_hour,
                domain.max_writes_per_hour,
                _DEFAULT_MAX_WRITES_PER_HOUR,
            ),
            max_push_bytes=_cascade(
                override.max_push_bytes, domain.max_push_bytes, _DEFAULT_MAX_PUSH_BYTES
            ),
        )

    def effective_actions(self, host: str) -> tuple[str, ...]:
        """Per-host action cascade, same ``_cascade`` mechanic as
        :meth:`effective_rules`: the endpoint's own ``actions`` if set, else
        ``git_actions`` (the domain default), else the built-in default
        (:data:`~warden.guards.git.actions.DEFAULT`). The list **replaces**
        completely — never merged, just like ``branch_prefixes``.

        The ``type``-cut applies only to an *inherited* value: a
        ``plain`` endpoint that falls through to ``git_actions``/the built-in
        default is silently intersected with its type's valid action ids
        (:data:`~warden.guards.git.endpoints.ENDPOINT_TYPES`) — no error,
        since every mixed deployment would otherwise be forced to override
        every ``plain`` endpoint explicitly. An *explicit* endpoint override
        is returned as-is, unfiltered here: a type-impossible id in it is a
        ``ConfigError`` raised by the loader at config-build time
        (``core.config_load``), never silently dropped.

        Deferred import (matching ``config_load._parse_actions``): the
        vocabulary is guard-owned, ``Config`` only threads the plain
        ``tuple[str, ...]`` values through.
        """
        from ..guards.git.actions import DEFAULT
        from ..guards.git.endpoints import ENDPOINT_TYPES

        endpoint = self.endpoint_for(host)
        override = endpoint.actions if endpoint is not None else None
        if override is not None:
            return override

        builtin_default = tuple(sorted(action.id for action in DEFAULT))
        inherited = _cascade(None, self.git_actions, builtin_default)
        if endpoint is None:
            return inherited
        valid_for_type = ENDPOINT_TYPES[endpoint.type].valid_action_ids
        return tuple(action for action in inherited if action in valid_for_type)

    def git_project_allowed(self, host: str, project: str) -> bool:
        """Per-endpoint ``allowed_projects`` check, keyed by host.

        An unconfigured host has no endpoint and is therefore denied — this is
        the per-endpoint analogue of :meth:`project_allowed`'s global check.
        """
        endpoint = self.endpoint_for(host)
        return endpoint is not None and endpoint.project_allowed(project)

    def access_mode(self, host: str) -> AccessMode:
        """Mode derived from which of this host's tokens are present.

        No read token means closed even if a write token exists — a write
        token is never used as a read fallback (least privilege).
        """
        creds = self.git_credentials.get(self.normalize_host(host), HostCredentials())
        if not creds.read_token:
            return "closed"
        if not creds.write_token:
            return "read-only"
        return "read-write"
