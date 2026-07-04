"""The typed, frozen Config value the policy consumes.

Only the *model* half of the config layer lives here — building a
:class:`Config` from env + ``warden.toml`` (secret files, precedence, hard
fail-closed validation, the three GITLAB_MODEs) is :mod:`warden.core.config_load`'s
job. Split kept so neither half outgrows a readable file and the many
``Config`` importers (guards, catalog, tests) depend on the small value type,
not on the loading machinery.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import Literal, Mapping, Optional, TypeVar
from urllib.parse import urlparse

_T = TypeVar("_T")


class ConfigError(RuntimeError):
    """Raised on invalid/missing configuration — the Warden refuses to start."""


@dataclass(frozen=True)
class HostCredentials:
    """One host's resolved read/write tokens (§07 Punkt 8 follow-up, design
    spike section 3). Every configured host gets an entry — including the
    first-listed one, whose tokens are simply an alias of the legacy
    ``GITLAB_READ_TOKEN``/``GITLAB_WRITE_TOKEN`` — so guard code never has to
    special-case "the primary host"."""

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
    plus optional rule overrides. ``allowed_projects`` is always per-endpoint —
    a project path is only unambiguous relative to the host it lives on."""

    host: str
    type: str
    allowed_projects: tuple[str, ...] = ()
    rules: GitRules = field(default_factory=GitRules)

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
    # Cheap, no packfile-parsing push-size cap (§07 Punkt 6.3): checked against
    # the receive-pack request's Content-Length before the body is streamed
    # upstream. Generous default so a normal push is never affected.
    max_push_bytes: int = 50 * 1024 * 1024
    allowed_projects: tuple[str, ...] = ()
    api_url: str = "https://gitlab.com/api/v4"
    read_token: str = ""
    write_token: str = ""
    reconcile_interval_s: int = 300
    state_db_path: str = "/var/lib/warden/state.db"
    audit_log_path: str = "/var/log/warden/audit.jsonl"
    log_path: str = "/var/log/warden/warden.log"
    agent_port: int = 8080
    admin_port: int = 9090
    admin_host: str = "0.0.0.0"
    gitlab_mode: str = "read-write"
    # The parsed [api.endpoints].enable list — plain data, catalog-agnostic.
    # None ⇒ section absent, meaning "use the catalog's default set"; an
    # explicit empty tuple disables every default entry. The gitlab_api guard
    # owns turning this into the request-matchable table (see
    # guards.gitlab_api.catalog.activation.build_effective_table) — config.py
    # itself never looks at the catalog.
    endpoint_enable: Optional[tuple[str, ...]] = None
    # [git.urls].hosts allowlist, in configured order (§07 Punkt 8 design
    # spike, docs/design/architecture-generalization/08-multi-target.md) —
    # host_order[0] is the host GITLAB_READ_TOKEN/GITLAB_WRITE_TOKEN alias
    # (see `host_credentials`), and the order `UpstreamRouter`/reconcile
    # iterate in. Empty (default) ⇒ multi-target inactive, single implicit
    # target, unchanged behaviour. `allowed_hosts` (below) is derived from
    # this field, not stored separately.
    host_order: tuple[str, ...] = ()
    # Per-host resolved tokens, keyed by normalised host. Empty when
    # `host_order` is empty; populated by config_load, including an entry
    # for `host_order[0]` that mirrors read_token/write_token.
    host_credentials: Mapping[str, HostCredentials] = field(default_factory=dict)
    # [git.rules] domain defaults and [[git.endpoint]] entries (one host each) —
    # the endpoint-taxonomy replacement for host_order/allowed_hosts, not yet
    # wired into any guard. Empty git_endpoints ⇒ no endpoints configured.
    git_rules: GitRules = field(default_factory=GitRules)
    git_endpoints: tuple[GitEndpoint, ...] = ()
    # Per-endpoint tokens resolved from the grouped read_tokens/write_tokens
    # files, keyed by normalised host. Backs access_mode(); not yet wired into
    # any guard.
    git_credentials: Mapping[str, HostCredentials] = field(default_factory=dict)

    @property
    def gitlab_enabled(self) -> bool:
        """True unless GitLab is intentionally disabled (GITLAB_MODE=off)."""
        return self.gitlab_mode != "off"

    @property
    def writes_enabled(self) -> bool:
        """True only in read-write mode — never in off or read-only."""
        return self.gitlab_mode == "read-write"

    @property
    def git_base(self) -> str:
        return self.api_url.removesuffix("/api/v4")

    def project_allowed(self, project: str) -> bool:
        """Default-deny match against ``ALLOWED_PROJECTS``, path form only.

        No prefix/subpath match — the allowlist names concrete projects, never
        group prefixes (README doctrine). GitLab also accepts a project's
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

    @functools.cached_property
    def allowed_hosts(self) -> frozenset[str]:
        """Membership set :meth:`host_allowed` checks, derived from
        ``host_order`` (§07 Punkt 8 follow-up) — one stored field, not two
        kept in sync by hand. Each entry is run through :meth:`normalize_host`
        so a differently-cased/ported/dotted ``host_order`` entry still
        matches the same normalised incoming ``Host`` header."""
        return frozenset(self.normalize_host(h) for h in self.host_order)

    def host_allowed(self, host: str) -> bool:
        """Host-header allowlist gate (§07 Punkt 8 design spike), wired into
        the kernel path via ``core.guard.host_gate``.

        Empty ``allowed_hosts`` (the default, and every deployment before
        multi-target) means the feature is off: always ``True``, no behaviour
        change. A non-empty allowlist switches to strict default-deny: only a
        listed host (case-insensitive, trailing dot and ``:port`` stripped)
        passes — everything else, including an empty ``host``, is denied.
        """
        if not self.allowed_hosts:
            return True
        normalized = self.normalize_host(host)
        return bool(normalized) and normalized in self.allowed_hosts

    @staticmethod
    def normalize_host(host: str) -> str:
        """Case/port/trailing-dot-insensitive host normalisation.

        The single definition shared by :meth:`host_allowed`,
        :class:`~warden.core.transport.UpstreamRouter`'s header lookup and
        every ``(host, project)`` state key — so the same raw ``Host`` header
        always maps to the same normalised key everywhere it is used.
        """
        return host.split(":", 1)[0].strip().lower().rstrip(".")

    @property
    def implicit_host(self) -> str:
        """The single-target state/reconcile host key, derived from
        ``api_url``/``GITLAB_URL`` (§07 Punkt 8 follow-up, design spike
        section 4, last paragraph). Used only when ``host_order`` is empty —
        a stable, deterministic value that does not depend on what ``Host``
        header a client happens to send, so single-target behaviour never
        changes because of a client detail multi-target introduced.
        """
        return urlparse(self.api_url).hostname or ""

    @property
    def effective_hosts(self) -> tuple[str, ...]:
        """Non-empty host list reconcile iterates over (§07 Punkt 8
        follow-up): ``host_order`` when multi-target is configured, otherwise
        the single ``implicit_host`` — reconcile and state keys never see an
        empty host list."""
        return self.host_order or (self.implicit_host,)

    def resolve_target_host(self, header: str) -> Optional[str]:
        """The canonical host key for state/reconcile/Upstream lookup, given
        a raw incoming ``Host`` header (§07 Punkt 8 follow-up).

        Single-target (``host_order`` empty): always :attr:`implicit_host`,
        independent of what the client sent — behaviour-neutral for every
        deployment before multi-target. Multi-target: the normalised header
        if it is a listed host, else ``None`` (unknown host — the caller must
        deny, never fabricate a key for it; ``core.guard.host_gate`` already
        denies this case earlier in the pipeline, so callers past that point
        should never actually observe ``None``).
        """
        if not self.host_order:
            return self.implicit_host
        normalized = self.normalize_host(header)
        return normalized if normalized in self.allowed_hosts else None

    def credentials_for(self, host: str) -> HostCredentials:
        """This host's read/write tokens. Falls back to the primary
        ``read_token``/``write_token`` for any host not present in
        ``host_credentials`` — which is exactly what happens in single-target
        mode, where ``host_credentials`` is empty and ``host`` is always
        :attr:`implicit_host`."""
        return self.host_credentials.get(host, HostCredentials(self.read_token, self.write_token))

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
