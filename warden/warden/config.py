"""Env → typed Config with hard fail-closed validation (W10).

Missing token or empty allowlist ⇒ abort, never "open". `ALLOWED_PROJECTS`
empty ⇒ nothing is allowed (fail-closed), not "everything".
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
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


def _secret(env: Mapping[str, str], name: str) -> str:
    """Read a secret from <name>_FILE (compose secret / mounted file) if set, else <name>.
    File wins so the running stack reads /run/secrets/…; the bare env var stays the fallback
    for tests and bare `docker run`. Trailing newline (common in token files) is stripped."""
    path = env.get(f"{name}_FILE")
    if path:
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError as e:
            raise ConfigError(f"{name}_FILE={path!r} unreadable: {e}") from e
    return env.get(name, "")


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(p.strip() for p in value.split(",") if p.strip())


DEFAULT_TOML_PATH = "/etc/warden/warden.toml"


def _load_toml(path: str) -> dict[str, object]:
    """Load non-secret tunables from warden.toml.

    Missing file ⇒ empty dict (env/defaults take over). Malformed file aborts —
    a silently-misparsed policy on the trust boundary is worse than a hard stop.
    """
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc


def from_env(
    env: Optional[Mapping[str, str]] = None,
    *,
    strict: bool = True,
    toml_path: Optional[str] = None,
) -> Config:
    """Build a :class:`Config`.

    **One source of truth per setting.** Non-secret policy tunables
    (``branch_prefix``, the ``max_*`` limits, ``allowed_projects``) live in
    ``warden.toml``; a matching env var **overrides** the file value when set
    (non-empty), else the file value applies, else a built-in default. Secrets
    (tokens) and infra (URL, host, ports, paths) come from env only.

    With ``strict`` (the production path) missing secrets or an empty project
    allowlist abort startup. Tests pass ``strict=False`` to build partial configs.
    """
    env = env if env is not None else os.environ
    file = _load_toml(toml_path or env.get("WARDEN_CONFIG_PATH", DEFAULT_TOML_PATH))

    def _int(key: str, default: int) -> int:
        raw = env.get(key)
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError as exc:
            raise ConfigError(f"{key} must be an integer, got {raw!r}") from exc

    # --- tunables: env (if set, non-empty) overrides warden.toml, else default ---
    def _tunable_str(env_key: str, toml_key: str, default: str) -> str:
        raw = env.get(env_key)
        if raw:
            return raw
        return str(file.get(toml_key, default))

    def _tunable_int(env_key: str, toml_key: str, default: int) -> int:
        raw = env.get(env_key)
        if raw:
            try:
                return int(raw)
            except ValueError as exc:
                raise ConfigError(f"{env_key} must be an integer, got {raw!r}") from exc
        val = file.get(toml_key, default)
        if not isinstance(val, int) or isinstance(val, bool):
            raise ConfigError(f"{toml_key} in warden.toml must be an integer, got {val!r}")
        return val

    def _tunable_projects(env_key: str, toml_key: str) -> tuple[str, ...]:
        raw = env.get(env_key)
        if raw:
            return _split_csv(raw)
        val = file.get(toml_key, [])
        if not isinstance(val, list) or not all(isinstance(p, str) for p in val):
            raise ConfigError(f"{toml_key} in warden.toml must be a list of strings, got {val!r}")
        return tuple(p.strip() for p in val if p.strip())

    cfg = Config(
        branch_prefix=_tunable_str("BRANCH_PREFIX", "branch_prefix", "claude/"),
        max_open_mrs=_tunable_int("MAX_OPEN_MRS", "max_open_mrs", 5),
        max_open_branches=_tunable_int("MAX_OPEN_BRANCHES", "max_open_branches", 10),
        max_writes_per_hour=_tunable_int("MAX_WRITES_PER_HOUR", "max_writes_per_hour", 60),
        allowed_projects=_tunable_projects("ALLOWED_PROJECTS", "allowed_projects"),
        api_url=env.get("GITLAB_URL", "https://gitlab.com").rstrip("/") + "/api/v4",
        read_token=_secret(env, "GITLAB_READ_TOKEN"),
        write_token=_secret(env, "GITLAB_WRITE_TOKEN"),
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
