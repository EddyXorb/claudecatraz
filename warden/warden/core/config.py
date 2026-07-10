"""The typed, frozen Config value the policy consumes.

Only the model half of the config layer lives here; building one from
env + warden.toml is config_load's job. There is no global "mode": access
is derived per host from which of that host's tokens are present.
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
    Config.git_credentials. Resolved from the grouped read_tokens/
    write_tokens secret files — a host with no entry (or a missing read
    token) is simply closed (Config.access_mode), never a crash."""

    read_token: str = ""
    write_token: str = ""


AccessMode = Literal["closed", "read-only", "read-write"]


def normalize_project(project: str) -> str:
    """Canonical project path: drop the git .git suffix and surrounding slashes.

    Normalising in one place keeps allowlist checks, REST project-ids,
    upstream URLs, and state keys consistent.
    """
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
    """Overridable git policy knobs. None means "not set here" so a cascade
    (endpoint override -> domain default -> built-in default) can tell that
    apart from an explicit, deliberately narrow value."""

    branch_prefixes: Optional[tuple[str, ...]] = None
    max_open_branches: Optional[int] = None
    max_open_mrs: Optional[int] = None
    max_writes_per_hour: Optional[int] = None
    max_push_bytes: Optional[int] = None


@dataclass(frozen=True)
class GitEndpoint:
    """One git host: its identity/scope plus optional rule/action overrides.

    actions follows the rules cascade: None means "inherit the domain or
    built-in default"; explicit () means "this endpoint may do nothing" —
    the two must stay distinguishable.
    """

    host: str
    type: str
    allowed_projects: tuple[str, ...] = ()
    rules: GitRules = field(default_factory=GitRules)
    actions: Optional[tuple[str, ...]] = None

    def project_allowed(self, project: str) -> bool:
        """Default-deny match against this endpoint's own allowed_projects."""
        project = normalize_project(project)
        return any(project == allowed.strip("/") for allowed in self.allowed_projects)


@dataclass(frozen=True)
class Config:
    branch_prefixes: tuple[str, ...] = ("claude/",)
    max_open_mrs: int = 5
    max_open_branches: int = 10
    max_writes_per_hour: int = 60
    # Cheap push-size cap checked against Content-Length before streaming.
    max_push_bytes: int = 50 * 1024 * 1024
    allowed_projects: tuple[str, ...] = ()
    reconcile_interval_s: int = 300
    state_db_path: str = "/var/lib/warden/state.db"
    audit_log_path: str = "/var/log/warden/audit.jsonl"
    log_path: str = "/var/log/warden/warden.log"
    agent_port: int = 8080
    admin_port: int = 9090
    admin_host: str = "0.0.0.0"
    # Every routable host is an explicit GitEndpoint; empty git_endpoints
    # means every host is denied (real default-deny, not "feature off").
    git_rules: GitRules = field(default_factory=GitRules)
    # None means the key is absent from warden.toml; the built-in default
    # applies. Whole-list granularity: actions replace, never merge per-key.
    git_actions: Optional[tuple[str, ...]] = None
    git_endpoints: tuple[GitEndpoint, ...] = ()
    # Per-endpoint tokens resolved from secret files, keyed by normalised host.
    git_credentials: Mapping[str, HostCredentials] = field(default_factory=dict)

    def project_allowed(self, project: str) -> bool:
        """Default-deny match against ALLOWED_PROJECTS, path form only.

        No prefix/subpath match. GitLab's numeric project ids are matched
        separately by the REST-API guard's own project_allowed, not here.
        """
        project = normalize_project(project)
        return any(project == allowed.strip("/") for allowed in self.allowed_projects)

    def in_branch_namespace(self, name: str) -> bool:
        """True iff name starts with any configured branch prefix.

        Single source of truth for the branch namespace: git guard's namespace
        checks and reconcile filters call this instead of comparing directly.
        """
        return any(name.startswith(prefix) for prefix in self.branch_prefixes)

    @staticmethod
    def normalize_host(host: str) -> str:
        """Case/port/trailing-dot-insensitive host normalisation.

        The single definition shared everywhere a raw Host header must map
        to a normalised key: host_allowed, UpstreamRouter, state keys.
        """
        return host.split(":", 1)[0].strip().lower().rstrip(".")

    def host_allowed(self, host: str) -> bool:
        """Host-header gate. Real default-deny: a host passes only if it has
        a configured endpoint and that endpoint has a usable read credential
        (access_mode is not "closed"). No "empty allowlist means allow" fallback.
        """
        normalized = self.normalize_host(host)
        return (
            bool(normalized)
            and normalized in self.git_allowed_hosts
            and self.access_mode(normalized) != "closed"
        )

    def resolve_target_host(self, header: str) -> Optional[str]:
        """The canonical host key for state/reconcile/Upstream lookup.

        The normalised header if it names a configured endpoint, else None
        (unknown host — the caller must deny, never fabricate a key).
        """
        normalized = self.normalize_host(header)
        return normalized if normalized in self.git_allowed_hosts else None

    @functools.cached_property
    def _endpoints_by_host(self) -> Mapping[str, GitEndpoint]:
        """Normalised-host -> endpoint lookup, built once from git_endpoints."""
        return {self.normalize_host(e.host): e for e in self.git_endpoints}

    def endpoint_for(self, host: str) -> Optional[GitEndpoint]:
        """The configured endpoint for this host, or None if none matches."""
        return self._endpoints_by_host.get(self.normalize_host(host))

    @functools.cached_property
    def git_allowed_hosts(self) -> frozenset[str]:
        """Normalised hosts with a configured [[git.endpoint]] entry."""
        return frozenset(self._endpoints_by_host)

    @property
    def effective_hosts(self) -> tuple[str, ...]:
        """Host list: every configured endpoint's normalised host, in
        git_endpoints order. Empty when no endpoint is configured. Includes
        closed endpoints; see open_hosts for the per-endpoint
        reconcile's trimmed variant."""
        return tuple(self.normalize_host(e.host) for e in self.git_endpoints)

    @property
    def open_hosts(self) -> tuple[str, ...]:
        """effective_hosts, trimmed to endpoints with a usable read
        credential. A closed endpoint is unreachable via host_gate anyway
        and never needs reconciling.
        """
        return tuple(
            self.normalize_host(e.host)
            for e in self.git_endpoints
            if self.access_mode(e.host) != "closed"
        )

    def effective_rules(self, host: str) -> GitRules:
        """Per-key cascade for host: endpoint override, else git_rules
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
        """Per-host action cascade: endpoint override, else domain default,
        else built-in default. Replaces completely, never merged.

        The type-cut applies only to an inherited value: it is silently
        intersected with the endpoint type's valid action ids. An explicit
        override is returned as-is — a type-impossible id in it is a
        ConfigError at config-build time, never silently dropped.
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
        """Per-endpoint allowed_projects check, keyed by host.

        An unconfigured host has no endpoint and is therefore denied — this is
        the per-endpoint analogue of project_allowed's global check.
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
