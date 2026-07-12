"""Env + warden.toml -> Config.

The loading half of the config layer: secret files, TOML parsing,
env-over-file precedence, and hard fail-closed validation. One source of
truth per setting: policy tunables live in warden.toml, credentials live
in the grouped read_tokens/write_tokens secret files. A host with no
usable read token simply resolves closed (fail-closed degrade, not
fail-stop) rather than aborting startup.
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

# Each removed top-level key maps to the table that owns it now. A leftover
# key is a startup error, never silently dropped: a stale quota would loosen
# and a stale allowlist would silently deny.
_REMOVED_TOP_LEVEL_KEYS = {
    "branch_prefixes": "[git.rules]",
    "branch_prefix": "[git.rules]",
    "max_open_mrs": "[git.rules]",
    "max_open_branches": "[git.rules]",
    "max_writes_per_hour": "[git.rules]",
    "max_push_bytes": "[git.rules]",
    "allowed_projects": "the owning [[git.endpoint]]",
}


def _secret(env: Mapping[str, str], name: str) -> str:
    """Read a secret from <name>_FILE (compose secret / mounted file) if set, else <name>.
    File wins so the running stack reads /run/secrets/…; the bare env var stays the fallback
    for tests and bare docker run. Trailing newline (common in token files) is stripped."""
    path = env.get(f"{name}_FILE")
    if path:
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError as e:
            raise ConfigError(f"{name}_FILE={path!r} unreadable: {e}") from e
    return env.get(name, "")


def _parse_actions(raw: object, context: str) -> Optional[tuple[str, ...]]:
    """Parse an actions list-key ([git].actions or a per-endpoint actions).

    Absent stays None (cascade falls through); present — including [] —
    becomes a tuple, kept distinguishable from absent. Fail-closed: not a
    list of strings, or an unknown action id, aborts startup.
    """
    if raw is None:
        return None
    if not isinstance(raw, list) or not all(isinstance(a, str) for a in raw):
        raise ConfigError(f"{context} must be a list of strings, got {raw!r}")

    from ..guards.git.actions import by_id

    unknown = sorted(set(raw) - set(by_id))
    if unknown:
        raise ConfigError(f"{context}: unknown action id(s) {unknown!r}")
    return tuple(raw)


def _parse_rules(table: Mapping[str, object], context: str) -> GitRules:
    """Parse a rules-shaped table into a GitRules override.

    An absent key stays None; an unknown key is a typo, not a
    silently-ignored setting, so it aborts startup.
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
    """Parse one [[git.endpoint]] table entry into a GitEndpoint."""
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
        from ..guards.git.endpoints import ENDPOINT_TYPES

        valid_for_type = ENDPOINT_TYPES[endpoint_type].valid_action_ids
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
    """Parse [git.rules]/[git].actions and [[git.endpoint]] entries.

    Fail-closed: an unknown type, a duplicate host, an unknown rules key,
    or an unknown/type-invalid actions id aborts startup.
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
    """Parse a grouped <host> <token> secrets file into a host -> token map.

    Blank lines and # comments are skipped; a duplicate host aborts startup.
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
    read_tokens/write_tokens files, keyed by normalised host."""
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
    """Log why each endpoint that resolves to closed is closed.

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


def _reject_removed_top_level_keys(file: Mapping[str, object]) -> None:
    """Fail-closed on a top-level key that moved into a git table.

    Each git knob has one home; a leftover top-level key names its new one
    rather than being silently ignored (a stale quota loosens, a stale
    allowlist denies).
    """
    for key, home in _REMOVED_TOP_LEVEL_KEYS.items():
        if key in file:
            raise ConfigError(f"warden.toml: top-level {key!r} moved to {home}")


def from_env(
    env: Optional[Mapping[str, str]] = None,
    *,
    strict: bool = True,
    toml_path: Optional[str] = None,
) -> Config:
    """Build a Config.

    One source of truth per setting: policy tunables come from warden.toml
    only, secrets and infra come from env only. With strict, a malformed
    warden.toml aborts startup, but there is no startup-fatal credential
    requirement — a host with no usable read token simply resolves closed.
    """
    env = env if env is not None else os.environ
    file = _load_toml(toml_path or env.get("WARDEN_CONFIG_PATH", DEFAULT_TOML_PATH))
    _reject_removed_top_level_keys(file)

    def _int(key: str, default: int) -> int:
        raw = env.get(key)
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError as exc:
            raise ConfigError(f"{key} must be an integer, got {raw!r}") from exc

    git_rules, git_actions, git_endpoints = _parse_git(file)
    git_credentials = _resolve_git_endpoint_credentials(env, git_endpoints)

    cfg = Config(
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
    """Fail-closed validation; every check here is unconditional.

    No credential or allowlist requirement aborts startup: a git_endpoint
    with a missing token just resolves closed, and an empty
    allowed_projects just means every op is denied until one is added.
    """
    problems: list[str] = []
    problems.extend(_quota_problems(cfg))
    problems.extend(_branch_prefixes_problems(cfg))

    if problems:
        raise ConfigError("invalid configuration: " + "; ".join(problems))


_QUOTA_KEYS = ("max_open_mrs", "max_open_branches", "max_writes_per_hour", "max_push_bytes")


def _quota_problems(cfg: Config) -> list[str]:
    """Fail-closed validation of every set quota knob.

    A non-positive ceiling in the global default or any endpoint override
    would deny every write; an unset knob falls back to the built-in.
    """
    sources = [("[git.rules]", cfg.git_rules)]
    for endpoint in cfg.git_endpoints:
        sources.append((f"[[git.endpoint]] host={endpoint.host!r} rules", endpoint.rules))
    problems: list[str] = []
    for label, rules in sources:
        for key in _QUOTA_KEYS:
            val = getattr(rules, key)
            if val is not None and val <= 0:
                problems.append(f"{label}.{key} must be > 0")
    return problems


def _branch_prefixes_problems(cfg: Config) -> list[str]:
    """Fail-closed validation of every enforced branch namespace.

    An empty list or empty element makes in_branch_namespace accept any
    name ("".startswith("") is always true); the global default and each
    endpoint override that sets it must be non-empty with no empty element.
    """
    sources = [("[git.rules].branch_prefixes", cfg.git_rules.branch_prefixes)]
    for endpoint in cfg.git_endpoints:
        label = f"[[git.endpoint]] host={endpoint.host!r} rules.branch_prefixes"
        sources.append((label, endpoint.rules.branch_prefixes))
    problems: list[str] = []
    for label, prefixes in sources:
        if prefixes is None:
            continue  # unset falls back to the non-empty built-in default
        if not prefixes:
            problems.append(f"{label} must be non-empty")
        elif any(not prefix for prefix in prefixes):
            problems.append(f"{label} entries must be non-empty")
    return problems
