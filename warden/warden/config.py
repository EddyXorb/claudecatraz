"""The typed, frozen Config value the policy consumes (W10).

Only the *model* half of the config layer lives here — building a
:class:`Config` from env + ``warden.toml`` (secret files, precedence, hard
fail-closed validation, the three GITLAB_MODEs) is :mod:`warden.config_load`'s
job. Split kept so neither half outgrows a readable file and the many
``Config`` importers (policy, catalog, proxies, tests) depend on the small
value type, not on the loading machinery.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # only for annotations; the real import is deferred (see below)
    from .catalog.activation import EffectiveTable
    from .catalog.config_parse import EndpointActivation


class ConfigError(RuntimeError):
    """Raised on invalid/missing configuration — the Warden refuses to start."""


def _default_endpoint_activation() -> "EndpointActivation":
    """Default ``endpoint_activation`` for a bare ``Config(...)`` (most tests
    build one this way). Imported lazily — not at module load time — so
    ``config.py`` never depends on the (much larger) ``catalog`` package at
    import time; see ``Config.effective_endpoints`` below for the same reason.
    """
    from .catalog.config_parse import EndpointActivation

    return EndpointActivation()


def normalize_project(project: str) -> str:
    """Canonical project path: drop the git ``.git`` suffix and surrounding slashes.

    The git Smart-HTTP path carries ``group/proj.git``; the allowlist and REST
    forms use the bare ``group/proj``. Normalising in one place keeps allowlist
    checks, REST project-ids, upstream URLs and state keys consistent (one
    definition), so a pushed branch is not counted twice in ``claude_branches``."""
    return project.removesuffix(".git").strip("/")


@dataclass(frozen=True)
class Config:
    branch_prefixes: tuple[str, ...] = ("claude/",)
    max_open_mrs: int = 5
    max_open_branches: int = 10
    max_writes_per_hour: int = 60
    allowed_projects: tuple[str, ...] = ()
    # Numeric project ids of ``allowed_projects``, resolved at reconcile. GitLab's
    # ``/projects/:id`` accepts the url-encoded path OR the numeric id, so the
    # allowlist must know both forms (filled in by AppContext.reconcile).
    allowed_project_ids: tuple[str, ...] = ()
    api_url: str = "https://gitlab.com/api/v4"
    read_token: str = ""
    write_token: str = ""
    reconcile_interval_s: int = 300
    state_db_path: str = "/var/lib/warden/state.db"
    audit_log_path: str = "/var/log/warden/audit.jsonl"
    agent_port: int = 8080
    admin_port: int = 9090
    admin_host: str = "0.0.0.0"
    gitlab_mode: str = "read-write"
    # §04.2/04.3: the raw, catalog-agnostic shape of [api.endpoints] — parsed
    # by from_env, validated against the catalog lazily (see
    # effective_endpoints below). Absent section ⇒ EndpointActivation() (its
    # own default), meaning "use the catalog's default set" (F4 hygiene: this
    # field is set once at construction and never replaced at runtime).
    endpoint_activation: "EndpointActivation" = field(default_factory=_default_endpoint_activation)

    @functools.cached_property
    def effective_endpoints(self) -> "EffectiveTable":
        """The built endpoint table (§04.2/04.3) — Catalog × this Config's
        ``endpoint_activation``, computed once and memoized (F4: this is a
        pure derivation of already-frozen fields, the same pattern as
        ``CatalogEntry.regex``, not a runtime mutation of policy).

        Raises :class:`ConfigError` on any fail-closed validation failure
        (§04.3). Deferred import: ``catalog`` is a much larger package than
        this module needs at load time, and importing it lazily here (instead
        of at module scope) keeps ``config.py`` from depending on it during
        Python's import bootstrap — this property runs long after both
        modules are fully loaded.
        """
        from .catalog import CatalogConfigError, build_effective_table

        try:
            return build_effective_table(self, self.endpoint_activation)
        except CatalogConfigError as exc:
            raise ConfigError(str(exc)) from exc

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
        """Default-deny match against ``ALLOWED_PROJECTS`` (Q9, A8, B4).

        A request may name the project by url-encoded path *or* by numeric id
        (GitLab treats them interchangeably). Match either: the path exactly
        (after normalisation), or the numeric id against the reconcile-resolved
        set. No prefix/subpath match — the allowlist names concrete projects,
        never group prefixes (README doctrine).
        """
        project = normalize_project(project)
        if project in self.allowed_project_ids:
            return True
        return any(project == allowed.strip("/") for allowed in self.allowed_projects)

    def in_branch_namespace(self, name: str) -> bool:
        """True iff ``name`` starts with any configured branch prefix (M2).

        The single source of truth for the branch namespace: the R2/R3 checks
        (:func:`policy.check_ref`, :func:`warden.catalog.checks.field_has_prefix`) and the
        reconcile filters (:meth:`context.AppContext.mr_owned_by_claude`,
        ``context.AppContext._list_claude_branches``) all call this instead of
        comparing against ``branch_prefixes`` themselves — one namespace union,
        no scattered ``startswith`` calls to drift out of sync (Clean Code
        vorarbeiten, ``docs/design/architecture-generalization/06-migration.md``).
        """
        return any(name.startswith(prefix) for prefix in self.branch_prefixes)
