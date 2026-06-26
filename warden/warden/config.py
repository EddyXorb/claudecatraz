"""Env → typed Config with hard fail-closed validation (W10).

Missing token or empty allowlist ⇒ abort, never "open". `ALLOWED_PROJECTS`
empty ⇒ nothing is allowed (fail-closed), not "everything".
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional


class ConfigError(RuntimeError):
    """Raised on invalid/missing configuration — the Warden refuses to start."""


def normalize_project(project: str) -> str:
    """Canonical project path: drop the git ``.git`` suffix and surrounding slashes.

    The git Smart-HTTP path carries ``group/proj.git``; the allowlist and REST
    forms use the bare ``group/proj``. Normalising in one place keeps allowlist
    checks, REST project-ids, upstream URLs and state keys consistent (one
    definition), so a pushed branch is not counted twice in ``claude_branches``."""
    return project.removesuffix(".git").strip("/")


@dataclass(frozen=True)
class Config:
    branch_prefix: str = "claude/"
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

    @property
    def git_base(self) -> str:
        return self.api_url.removesuffix("/api/v4")

    def project_allowed(self, project: str) -> bool:
        """Default-deny match against ``ALLOWED_PROJECTS`` (Q9).

        A request may name the project by url-encoded path *or* by numeric id
        (GitLab treats them interchangeably). Match either: the path by
        exact/prefix, or the numeric id against the reconcile-resolved set.
        """
        project = normalize_project(project)
        if project in self.allowed_project_ids:
            return True
        for allowed in self.allowed_projects:
            allowed = allowed.strip("/")
            if project == allowed or project.startswith(allowed + "/"):
                return True
        return False


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(p.strip() for p in value.split(",") if p.strip())


def from_env(env: Optional[Mapping[str, str]] = None, *, strict: bool = True) -> Config:
    """Build a :class:`Config` from environment variables.

    With ``strict`` (the production path) missing secrets or an empty project
    allowlist abort startup. Tests pass ``strict=False`` to build partial configs.
    """
    env = env if env is not None else os.environ

    def _int(key: str, default: int) -> int:
        raw = env.get(key)
        if raw is None or raw == "":
            return default
        try:
            return int(raw)
        except ValueError as exc:
            raise ConfigError(f"{key} must be an integer, got {raw!r}") from exc

    cfg = Config(
        branch_prefix=env.get("BRANCH_PREFIX", "claude/"),
        max_open_mrs=_int("MAX_OPEN_MRS", 5),
        max_open_branches=_int("MAX_OPEN_BRANCHES", 10),
        max_writes_per_hour=_int("MAX_WRITES_PER_HOUR", 60),
        allowed_projects=_split_csv(env.get("ALLOWED_PROJECTS", "")),
        api_url=env.get("GITLAB_URL", "https://gitlab.com").rstrip("/") + "/api/v4",
        read_token=env.get("GITLAB_READ_TOKEN", ""),
        write_token=env.get("GITLAB_WRITE_TOKEN", ""),
        reconcile_interval_s=_int("RECONCILE_INTERVAL_S", 300),
        state_db_path=env.get("STATE_DB_PATH", "/var/lib/warden/state.db"),
        audit_log_path=env.get("AUDIT_LOG_PATH", "/var/log/warden/audit.jsonl"),
        agent_port=_int("AGENT_PORT", 8080),
        admin_port=_int("ADMIN_PORT", 9090),
        admin_host=env.get("ADMIN_HOST", "0.0.0.0"),
    )

    if strict:
        _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    problems: list[str] = []
    if not cfg.read_token:
        problems.append("GITLAB_READ_TOKEN is required")
    if not cfg.write_token:
        problems.append("GITLAB_WRITE_TOKEN is required")
    if not cfg.allowed_projects:
        problems.append("ALLOWED_PROJECTS must be non-empty (fail-closed)")
    if not cfg.branch_prefix:
        problems.append("BRANCH_PREFIX must be non-empty")
    for name in ("max_open_mrs", "max_open_branches", "max_writes_per_hour"):
        if getattr(cfg, name) <= 0:
            problems.append(f"{name.upper()} must be > 0")
    if problems:
        raise ConfigError("invalid configuration: " + "; ".join(problems))
