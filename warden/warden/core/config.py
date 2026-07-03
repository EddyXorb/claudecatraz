"""The typed, frozen Config value the policy consumes.

Only the *model* half of the config layer lives here — building a
:class:`Config` from env + ``warden.toml`` (secret files, precedence, hard
fail-closed validation, the three GITLAB_MODEs) is :mod:`warden.core.config_load`'s
job. Split kept so neither half outgrows a readable file and the many
``Config`` importers (guards, catalog, tests) depend on the small value type,
not on the loading machinery.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


class ConfigError(RuntimeError):
    """Raised on invalid/missing configuration — the Warden refuses to start."""


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
    allowed_projects: tuple[str, ...] = ()
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
    # The parsed [api.endpoints].enable list — plain data, catalog-agnostic.
    # None ⇒ section absent, meaning "use the catalog's default set"; an
    # explicit empty tuple disables every default entry. The gitlab_api guard
    # owns turning this into the request-matchable table (see
    # guards.gitlab_api.catalog.activation.build_effective_table) — config.py
    # itself never looks at the catalog.
    endpoint_enable: Optional[tuple[str, ...]] = None

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
        forge concept (the id is only known after reconcile talks to
        GitLab) — see ``guards.gitlab.forge.GitForge.project_allowed_by_id``,
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
