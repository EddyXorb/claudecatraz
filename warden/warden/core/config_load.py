"""Env + warden.toml → :class:`~warden.core.config.Config`.

The loading half of the config layer: :mod:`warden.core.config` holds the pure,
frozen :class:`~warden.core.config.Config` value (what the policy consumes);
this module holds everything that *produces* one — secret files, TOML parsing,
env-over-file precedence, and the hard fail-closed validation.

One source of truth per setting: policy tunables (branch namespace,
quotas, allowlists) live only in ``warden.toml``'s ``[git.rules]``/
``[[git.endpoint]]``; forge identity/credentials live only in the grouped
``read_tokens``/``write_tokens`` secret files. There is no global operating
mode and no startup-fatal token requirement — a per-host access mode
(:meth:`~warden.core.config.Config.access_mode`) is derived purely from which
of that host's tokens are present. A host with no usable read token is simply
``closed`` (a logged warning, see :func:`_warn_closed_endpoints`), never a
startup abort: the warden still boots (fail-closed *degrade*, not fail-stop)
and every operation against that host is denied by ``core.guard.host_gate``.
"""

from __future__ import annotations

import logging
import os
import tomllib
from pathlib import Path
from typing import Mapping, Optional

from .config import Config, ConfigError, GitEndpoint, GitRules, HostCredentials

log = logging.getLogger("warden")

_KNOWN_RULE_KEYS = frozenset(
    {
        "branch_prefixes",
        "max_open_branches",
        "max_open_mrs",
        "max_writes_per_hour",
        "max_push_bytes",
    }
)
_IMPLEMENTED_ENDPOINT_TYPES = frozenset({"gitlab", "plain"})
_RESERVED_ENDPOINT_TYPES = frozenset({"github"})


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


def _parse_actions(raw: object, context: str) -> Optional[tuple[str, ...]]:
    """Parse an ``actions`` list-key (``[git].actions`` or a per-endpoint
    ``actions``).

    Absent (``raw is None``, the key isn't in the table) stays ``None`` — "no
    opinion here, the cascade falls through". Present — including
    ``[]`` — becomes a tuple, deliberately kept distinguishable from absent
    (an explicit empty list means "may do nothing", not "inherit").

    Fail-closed: not a list of strings, or an id outside the closed
    vocabulary (typo protection), aborts startup. Deferred import: the
    vocabulary is guard-owned, core stays guard-agnostic at module-import time.
    """
    if raw is None:
        return None
    if not isinstance(raw, list) or not all(isinstance(a, str) for a in raw):
        raise ConfigError(f"{context} must be a list of strings, got {raw!r}")

    from ..guards.gitlab_api.actions import ALL_ACTIONS

    unknown = sorted(set(raw) - ALL_ACTIONS)
    if unknown:
        raise ConfigError(f"{context}: unknown action id(s) {unknown!r}")
    return tuple(raw)


def _parse_rules(table: Mapping[str, object], context: str) -> GitRules:
    """Parse a ``rules``-shaped table (``[git.rules]`` or an endpoint's inline
    ``rules = {...}``) into a :class:`GitRules` override.

    An absent key stays ``None`` (no opinion at this level); an unknown key is a
    typo, not a silently-ignored setting, so it aborts startup.
    """
    unknown = set(table) - _KNOWN_RULE_KEYS
    if unknown:
        raise ConfigError(f"{context}: unknown key(s) {sorted(unknown)!r}")

    branch_prefixes: Optional[tuple[str, ...]] = None
    raw_prefixes = table.get("branch_prefixes")
    if raw_prefixes is not None:
        if not isinstance(raw_prefixes, list) or not all(isinstance(p, str) for p in raw_prefixes):
            raise ConfigError(
                f"{context}.branch_prefixes must be a list of strings, got {raw_prefixes!r}"
            )
        branch_prefixes = tuple(raw_prefixes)

    ints: dict[str, Optional[int]] = {}
    for key in ("max_open_branches", "max_open_mrs", "max_writes_per_hour", "max_push_bytes"):
        val = table.get(key)
        if val is not None and (not isinstance(val, int) or isinstance(val, bool)):
            raise ConfigError(f"{context}.{key} must be an integer, got {val!r}")
        ints[key] = val

    return GitRules(
        branch_prefixes=branch_prefixes,
        max_open_branches=ints["max_open_branches"],
        max_open_mrs=ints["max_open_mrs"],
        max_writes_per_hour=ints["max_writes_per_hour"],
        max_push_bytes=ints["max_push_bytes"],
    )


def _parse_endpoint(raw: object, index: int) -> GitEndpoint:
    """Parse one ``[[git.endpoint]]`` table entry into a :class:`GitEndpoint`."""
    if not isinstance(raw, Mapping):
        raise ConfigError(f"warden.toml [[git.endpoint]] #{index}: must be a table")

    host = raw.get("host")
    if not isinstance(host, str) or not host.strip():
        raise ConfigError(f"warden.toml [[git.endpoint]] #{index}: host must be a non-empty string")
    host = host.strip()

    endpoint_type = raw.get("type")
    if endpoint_type in _RESERVED_ENDPOINT_TYPES:
        raise ConfigError(
            f"warden.toml [[git.endpoint]] host={host!r}: type {endpoint_type!r} is not "
            "implemented yet"
        )
    if not isinstance(endpoint_type, str) or endpoint_type not in _IMPLEMENTED_ENDPOINT_TYPES:
        raise ConfigError(
            f"warden.toml [[git.endpoint]] host={host!r}: unknown type {endpoint_type!r}, "
            f"must be one of {sorted(_IMPLEMENTED_ENDPOINT_TYPES)!r}"
        )

    raw_projects = raw.get("allowed_projects", [])
    if not isinstance(raw_projects, list) or not all(isinstance(p, str) for p in raw_projects):
        raise ConfigError(
            f"warden.toml [[git.endpoint]] host={host!r}: allowed_projects must be a list of "
            f"strings, got {raw_projects!r}"
        )

    rules_table = raw.get("rules", {})
    if not isinstance(rules_table, Mapping):
        raise ConfigError(f"warden.toml [[git.endpoint]] host={host!r}: rules must be a table")

    actions = _parse_actions(
        raw.get("actions"), f"warden.toml [[git.endpoint]] host={host!r} actions"
    )
    if actions is not None:
        # Explicit endpoint override with a type-impossible id is always a
        # mistake — unlike the inherited default (cut quietly in
        # Config.effective_actions), this aborts startup here.
        from ..guards.gitlab_api.actions import actions_valid_for_type

        valid_for_type = actions_valid_for_type(endpoint_type)
        invalid = sorted(set(actions) - valid_for_type)
        if invalid:
            raise ConfigError(
                f"warden.toml [[git.endpoint]] host={host!r}: action id(s) {invalid!r} "
                f"not valid for type {endpoint_type!r}"
            )

    return GitEndpoint(
        host=host,
        type=endpoint_type,
        allowed_projects=tuple(p.strip() for p in raw_projects if p.strip()),
        rules=_parse_rules(rules_table, f"warden.toml [[git.endpoint]] host={host!r} rules"),
        actions=actions,
    )


def _parse_git(
    file: Mapping[str, object],
) -> tuple[GitRules, Optional[tuple[str, ...]], tuple[GitEndpoint, ...]]:
    """Parse ``[git.rules]``/``[git].actions`` (domain defaults) and
    ``[[git.endpoint]]`` (one entry per host) into the endpoint-taxonomy config
    surface.

    Fail-closed: an unknown ``type``, a duplicate ``host``, an unknown key in
    any ``rules`` table, or an unknown/type-invalid ``actions`` id aborts
    startup rather than silently misconfiguring policy.
    """
    git = file.get("git", {})
    if not isinstance(git, Mapping):
        raise ConfigError("warden.toml: [git] must be a table")

    rules_table = git.get("rules", {})
    if not isinstance(rules_table, Mapping):
        raise ConfigError("warden.toml: [git.rules] must be a table")
    git_rules = _parse_rules(rules_table, "warden.toml [git.rules]")

    git_actions = _parse_actions(git.get("actions"), "warden.toml [git].actions")

    raw_endpoints = git.get("endpoint", [])
    if not isinstance(raw_endpoints, list):
        raise ConfigError("warden.toml: [[git.endpoint]] must be an array of tables")

    endpoints: list[GitEndpoint] = []
    seen_hosts: dict[str, str] = {}
    for index, raw in enumerate(raw_endpoints):
        endpoint = _parse_endpoint(raw, index)
        normalized = Config.normalize_host(endpoint.host)
        if normalized in seen_hosts:
            raise ConfigError(
                f"warden.toml [[git.endpoint]]: duplicate host {endpoint.host!r} "
                f"(already configured as {seen_hosts[normalized]!r})"
            )
        seen_hosts[normalized] = endpoint.host
        endpoints.append(endpoint)

    return git_rules, git_actions, tuple(endpoints)


def _parse_token_file(env: Mapping[str, str], name: str) -> dict[str, str]:
    """Parse a grouped ``<host> <token>`` secrets file (``<name>_FILE`` or the
    bare ``<name>`` env var, via :func:`_secret`) into a host -> token map.

    Split on the first run of whitespace; blank lines and ``#`` comments are
    skipped; a duplicate host (after normalisation) aborts startup.
    """
    content = _secret(env, name)
    if not content:
        return {}
    tokens: dict[str, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(None, 1)
        if len(parts) != 2:
            raise ConfigError(f"{name}_FILE: malformed line {line!r}, expected '<host> <token>'")
        host = Config.normalize_host(parts[0])
        if host in tokens:
            raise ConfigError(f"{name}_FILE: duplicate host {host!r}")
        tokens[host] = parts[1]
    return tokens


def _resolve_git_endpoint_credentials(
    env: Mapping[str, str], git_endpoints: tuple[GitEndpoint, ...]
) -> dict[str, HostCredentials]:
    """Resolve each endpoint's read/write tokens from the grouped
    ``read_tokens``/``write_tokens`` files, keyed by normalised host."""
    read_tokens = _parse_token_file(env, "READ_TOKENS")
    write_tokens = _parse_token_file(env, "WRITE_TOKENS")
    creds: dict[str, HostCredentials] = {}
    for endpoint in git_endpoints:
        host = Config.normalize_host(endpoint.host)
        creds[host] = HostCredentials(
            read_token=read_tokens.get(host, ""), write_token=write_tokens.get(host, "")
        )
    return creds


def _warn_closed_endpoints(cfg: Config) -> None:
    """Log why each endpoint that resolves to ``closed`` is closed.

    Fail-closed-degrade, not fail-stop: a missing or write-without-read
    credential never aborts startup, it only disables that one endpoint.
    """
    for endpoint in cfg.git_endpoints:
        if cfg.access_mode(endpoint.host) != "closed":
            continue
        creds = cfg.git_credentials.get(Config.normalize_host(endpoint.host), HostCredentials())
        if creds.write_token:
            log.warning(
                "host %s closed: write token without a read token — add a "
                "read-scoped token to read_tokens",
                endpoint.host,
            )
        else:
            log.warning("host %s closed: no read token", endpoint.host)


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

    **One source of truth per setting.** Policy tunables
    (``branch_prefixes``, the ``max_*`` limits, ``allowed_projects``) come from
    ``warden.toml`` only — no env override left. Secrets (the grouped
    ``read_tokens``/``write_tokens`` files) and infra (host, ports, paths)
    come from env only.

    With ``strict`` (the production path) a malformed ``warden.toml`` still
    aborts startup — a non-positive quota or an empty/blank branch-prefix
    namespace is nonsensical regardless of deployment. There is no
    startup-fatal *credential* requirement: a host with no usable read
    token simply resolves ``closed`` (fail-closed *degrade*, logged by
    :func:`_warn_closed_endpoints`), and a deployment with no endpoints at all
    boots and denies everything via ``core.guard.host_gate``.
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

    # --- tunables: warden.toml only, no env override ---
    def _tunable_int(toml_key: str, default: int) -> int:
        val = file.get(toml_key, default)
        if not isinstance(val, int) or isinstance(val, bool):
            raise ConfigError(f"{toml_key} in warden.toml must be an integer, got {val!r}")
        return val

    def _tunable_branch_prefixes(
        list_key: str, legacy_scalar_key: str, default: tuple[str, ...]
    ) -> tuple[str, ...]:
        """Resolve ``branch_prefixes`` — one namespace-list setting, one source.

        ``warden.toml`` may set the list form (``branch_prefixes = [...]``) or
        the legacy scalar form (``branch_prefix = "..."``, kept as a
        one-element list) — never both, that would be two sources of truth
        for the same namespace. Emptiness (empty list, empty element) is *not*
        rejected here — :func:`_validate` does that so the error message is
        consistent whichever source produced the value.
        """
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

    def _tunable_projects(toml_key: str) -> tuple[str, ...]:
        val = file.get(toml_key, [])
        if not isinstance(val, list) or not all(isinstance(p, str) for p in val):
            raise ConfigError(f"{toml_key} in warden.toml must be a list of strings, got {val!r}")
        return tuple(p.strip() for p in val if p.strip())

    git_rules, git_actions, git_endpoints = _parse_git(file)
    git_credentials = _resolve_git_endpoint_credentials(env, git_endpoints)

    cfg = Config(
        branch_prefixes=_tunable_branch_prefixes("branch_prefixes", "branch_prefix", ("claude/",)),
        max_open_mrs=_tunable_int("max_open_mrs", 5),
        max_open_branches=_tunable_int("max_open_branches", 10),
        max_writes_per_hour=_tunable_int("max_writes_per_hour", 60),
        max_push_bytes=_tunable_int("max_push_bytes", 50 * 1024 * 1024),
        allowed_projects=_tunable_projects("allowed_projects"),
        reconcile_interval_s=_int("RECONCILE_INTERVAL_S", 300),
        state_db_path=env.get("STATE_DB_PATH", "/var/lib/warden/state.db"),
        audit_log_path=env.get("AUDIT_LOG_PATH", "/var/log/warden/audit.jsonl"),
        agent_port=_int("AGENT_PORT", 8080),
        admin_port=_int("ADMIN_PORT", 9090),
        admin_host=env.get("ADMIN_HOST", "0.0.0.0"),
        git_rules=git_rules,
        git_actions=git_actions,
        git_endpoints=git_endpoints,
        git_credentials=git_credentials,
    )
    _warn_closed_endpoints(cfg)

    if strict:
        _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    """Fail-closed validation with no mode-branching: every check here is
    unconditional, since there is no declared off/read-only/read-write mode
    to gate them on.

    No credential (or allowlist) requirement aborts startup — a git_endpoint
    with a missing/inconsistent token is fail-closed-*degrade*: the endpoint
    simply resolves ``closed`` (a logged warning, see
    ``_warn_closed_endpoints``), never an abort; and an empty ``allowed_projects``
    just means ``project_allowed()`` denies everything, so the warden boots
    (dev-env runs offline) and every op stays denied until a project is
    allowed.
    """
    problems: list[str] = []

    for name in ("max_open_mrs", "max_open_branches", "max_writes_per_hour", "max_push_bytes"):
        if getattr(cfg, name) <= 0:
            problems.append(f"{name.upper()} must be > 0")

    problems.extend(_branch_prefixes_problems(cfg))

    if problems:
        raise ConfigError("invalid configuration: " + "; ".join(problems))


def _branch_prefixes_problems(cfg: Config) -> list[str]:
    """Fail-closed validation of the branch namespace.

    An empty list or an empty element would make :meth:`Config.in_branch_namespace`
    accept *any* branch name (``"".startswith("")`` is always true) — checked once
    here, unconditionally (:func:`_validate`).
    """
    if not cfg.branch_prefixes:
        return ["BRANCH_PREFIX must be non-empty"]
    if any(not prefix for prefix in cfg.branch_prefixes):
        return ["BRANCH_PREFIX entries must be non-empty"]
    return []
