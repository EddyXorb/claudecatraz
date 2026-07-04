"""The typed, frozen Config value the policy consumes.

Only the *model* half of the config layer lives here — building a
:class:`Config` from env + ``warden.toml`` (secret files, precedence, hard
fail-closed validation, the three GITLAB_MODEs) is :mod:`warden.core.config_load`'s
job. Split kept so neither half outgrows a readable file and the many
``Config`` importers (guards, catalog, tests) depend on the small value type,
not on the loading machinery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional
from urllib.parse import urlparse


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


def normalize_project(project: str) -> str:
    """Canonical project path: drop the git ``.git`` suffix and surrounding slashes.

    The git Smart-HTTP path carries ``group/proj.git``; the allowlist and REST
    forms use the bare ``group/proj``. Normalising in one place keeps allowlist
    checks, REST project-ids, upstream URLs and state keys consistent (one
    definition), so a pushed branch is not counted twice in ``agent_branches``."""
    return project.removesuffix(".git").strip("/")


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
    # The parsed [git.urls].hosts allowlist (§07 Punkt 8 design spike,
    # docs/design/architecture-generalization/08-multi-target.md). Empty
    # (default) means the feature is inactive: single implicit target,
    # behaviour unchanged from before multi-target. Wired into the
    # request/kernel path via `host_gate` (core/guard.py) and
    # `UpstreamRouter` (core/transport.py) — see 08-multi-target.md section 6
    # for what "wired" now means.
    allowed_hosts: frozenset[str] = frozenset()
    # Order-preserving twin of `allowed_hosts` (§07 Punkt 8 follow-up): the
    # first entry is the host that GITLAB_READ_TOKEN/GITLAB_WRITE_TOKEN alias
    # (see `host_credentials`) and the one `UpstreamRouter`/reconcile iterate
    # in. `allowed_hosts` (frozenset, order-less) stays the fast membership
    # check `host_allowed` uses. Empty ⇒ multi-target inactive, identical to
    # today. config_load keeps both in sync (`allowed_hosts == frozenset(host_order)`).
    host_order: tuple[str, ...] = ()
    # Per-host resolved tokens, keyed by normalised host (§07 Punkt 8
    # follow-up, design spike section 3). Empty when `host_order` is empty.
    # Populated by config_load, including an entry for `host_order[0]` that
    # mirrors read_token/write_token.
    host_credentials: Mapping[str, HostCredentials] = field(default_factory=dict)

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
