"""Env → typed Config with hard fail-closed validation (W10).

Missing token ⇒ abort, never "open". `ALLOWED_PROJECTS` empty ⇒ nothing is
allowed (fail-closed *by denying*, not by crashing): the warden still boots so
the dev-env can run offline, and every GitLab op is denied until a project is
allowed. Fail-closed means *deny*, not *refuse to start*.

GITLAB_MODE selects one of three operating modes:
  off         — GitLab is intentionally disabled; no token or allowlist required;
                all GitLab operations are denied (R0).
  read-only   — only the read token is required; writes are denied (R0).
  read-write  — both tokens are required; full current behaviour (default).
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


_VALID_MODES = frozenset({"off", "read-only", "read-write"})


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
        (:func:`policy.check_ref`, :func:`api_endpoints.field_has_prefix`) and the
        reconcile filters (:meth:`context.AppContext.mr_owned_by_claude`,
        ``context.AppContext._list_claude_branches``) all call this instead of
        comparing against ``branch_prefixes`` themselves — one namespace union,
        no scattered ``startswith`` calls to drift out of sync (Clean Code
        vorarbeiten, ``docs/design/architecture-generalization/06-migration.md``).
        """
        return any(name.startswith(prefix) for prefix in self.branch_prefixes)


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
    (``branch_prefixes``, the ``max_*`` limits, ``allowed_projects``) live in
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

    def _tunable_branch_prefixes(
        env_key: str, list_key: str, legacy_scalar_key: str, default: tuple[str, ...]
    ) -> tuple[str, ...]:
        """Resolve ``branch_prefixes`` — one namespace-list setting, one source.

        CSV env var (if non-empty) wins outright. Else ``warden.toml`` may set
        the list form (``branch_prefixes = [...]``) or the legacy scalar form
        (``branch_prefix = "..."``, kept as a one-element list) — never both,
        that would be two sources of truth for the same namespace. Emptiness
        (empty list, empty element) is *not* rejected here — :func:`_validate`
        does that so the error message is consistent whichever source produced
        the value.
        """
        raw = env.get(env_key)
        if raw:
            return _split_csv(raw)
        has_list = list_key in file
        has_scalar = legacy_scalar_key in file
        if has_list and has_scalar:
            raise ConfigError(
                f"warden.toml: set only one of {list_key!r} or the legacy "
                f"{legacy_scalar_key!r}, not both"
            )
        if has_list:
            val = file[list_key]
            if not isinstance(val, list) or not all(isinstance(p, str) for p in val):
                raise ConfigError(
                    f"{list_key} in warden.toml must be a list of strings, got {val!r}"
                )
            return tuple(val)
        if has_scalar:
            val = file[legacy_scalar_key]
            if not isinstance(val, str):
                raise ConfigError(
                    f"{legacy_scalar_key} in warden.toml must be a string, got {val!r}"
                )
            return (val,)
        return default

    def _tunable_projects(env_key: str, toml_key: str) -> tuple[str, ...]:
        raw = env.get(env_key)
        if raw:
            return _split_csv(raw)
        val = file.get(toml_key, [])
        if not isinstance(val, list) or not all(isinstance(p, str) for p in val):
            raise ConfigError(f"{toml_key} in warden.toml must be a list of strings, got {val!r}")
        return tuple(p.strip() for p in val if p.strip())

    cfg = Config(
        branch_prefixes=_tunable_branch_prefixes(
            "BRANCH_PREFIX", "branch_prefixes", "branch_prefix", ("claude/",)
        ),
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
        gitlab_mode=(env.get("GITLAB_MODE") or "read-write").strip(),
    )

    if strict:
        _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    problems: list[str] = []

    if cfg.gitlab_mode not in _VALID_MODES:
        problems.append(
            f"GITLAB_MODE must be one of {sorted(_VALID_MODES)!r}, got {cfg.gitlab_mode!r}"
        )
        # Mode is unknown — skip mode-specific checks to avoid confusing secondary errors.
        raise ConfigError("invalid configuration: " + "; ".join(problems))

    # Quota limits apply in all modes.
    for name in ("max_open_mrs", "max_open_branches", "max_writes_per_hour"):
        if getattr(cfg, name) <= 0:
            problems.append(f"{name.upper()} must be > 0")

    if cfg.gitlab_mode == "off":
        # Intentionally disabled — no token or allowlist requirement.
        pass

    elif cfg.gitlab_mode == "read-only":
        if not cfg.read_token:
            problems.append("GITLAB_READ_TOKEN is required")
        problems.extend(_branch_prefixes_problems(cfg))
        # An empty allowlist is NOT a startup error: project_allowed() already
        # denies everything, so the warden boots (dev-env runs offline) and
        # simply refuses every GitLab op until a project is allowed.

    else:  # read-write (default)
        if not cfg.read_token:
            problems.append("GITLAB_READ_TOKEN is required")
        if not cfg.write_token:
            problems.append("GITLAB_WRITE_TOKEN is required")
        problems.extend(_branch_prefixes_problems(cfg))
        # Empty allowlist ⇒ deny-all (see read-only note above), not an abort.

    if problems:
        raise ConfigError("invalid configuration: " + "; ".join(problems))


def _branch_prefixes_problems(cfg: Config) -> list[str]:
    """Fail-closed validation of the branch namespace (shared by both live modes).

    An empty list or an empty element would make :meth:`Config.in_branch_namespace`
    accept *any* branch name (``"".startswith("")`` is always true) — checked once
    here instead of duplicated per mode.
    """
    if not cfg.branch_prefixes:
        return ["BRANCH_PREFIX must be non-empty"]
    if any(not prefix for prefix in cfg.branch_prefixes):
        return ["BRANCH_PREFIX entries must be non-empty"]
    return []
