import argparse
import re
from pathlib import Path
from typing import Any, cast

from catraz.envfile import unset_env_keys
from catraz.policy import (
    _discover_gitlab_projects,
    _resolve_allowed_projects,
    ensure_git_endpoint,
    normalize_host,
    remove_toml_key,
    set_toml_list,
    validate_project,
)
from catraz.ui import Out

from ._secrets import (
    _ensure_secret,
    _read_grouped_token,
    _upsert_grouped_token,
    _write_secret_value,
)


def _read_branch_prefix(warden_toml: Path | None) -> str:
    """Current (first) branch prefix, shown as the wizard's default.

    Reads warden.toml — either the list form (`branch_prefixes =
    [...]`) or the scalar form (`branch_prefix = "..."`); the wizard only
    ever prompts for one, but reads whichever form is on disk."""
    if not warden_toml or not warden_toml.exists():
        return "claude/"
    text = warden_toml.read_text(encoding="utf-8")
    m = re.search(r'branch_prefixes\s*=\s*\[\s*"([^"]*)"', text)
    if m:
        return m.group(1)
    m = re.search(r'branch_prefix\s*=\s*"([^"]*)"', text)
    return m.group(1) if m else "claude/"


def _read_endpoint_host(warden_toml: Path | None) -> str | None:
    """First configured `[[git.endpoint]]` host, or None when none is set —
    a fresh repo has no endpoint until the wizard adds one."""
    if not warden_toml or not warden_toml.exists():
        return None
    import tomllib

    try:
        git = tomllib.loads(warden_toml.read_text(encoding="utf-8")).get("git", {})
    except (tomllib.TOMLDecodeError, OSError):
        return None
    endpoints = git.get("endpoint", []) if isinstance(git, dict) else []
    for endpoint in endpoints if isinstance(endpoints, list) else []:
        host = endpoint.get("host") if isinstance(endpoint, dict) else None
        if isinstance(host, str) and host.strip():
            return host.strip()
    return None


def _inh_env(inherited: dict[str, Any] | None, key: str) -> str:
    """Return inherited .env value for *key*, or '' if not inherited."""
    if inherited is None:
        return ""
    return cast(str, inherited.get("env", {}).get(key, ""))


def _prompt_auth_mode(
    env: dict[str, str],
    args: argparse.Namespace,
    out: Out,
    inherited: dict[str, Any] | None = None,
) -> str:
    inh = _inh_env(inherited, "AUTH_MODE")
    auth_mode = inh or env.get("AUTH_MODE") or "subscription"
    has_from = inherited is not None
    if args.force or "AUTH_MODE" not in env or has_from:
        auth_mode = out.choice(
            "Claude auth mode?",
            [
                ("subscription", "subscription — import host ~/.claude (default)"),
                ("api_key", "api_key — dedicated Anthropic API key"),
            ],
            default=0 if auth_mode == "subscription" else 1,
        )
    return auth_mode


def _prompt_write_access(out: Out) -> bool:
    """Read-only vs read-write is just "did the user give a write token"; ask it
    directly instead of storing a mode."""
    return (
        cast(
            str,
            out.choice(
                "GitLab access?",
                [
                    ("read-write", "read-write — read + push (needs read & write tokens)"),
                    ("read-only", "read-only — read only (needs a read token)"),
                ],
                default=0,
            ),
        )
        == "read-write"
    )


def _prompt_configure_endpoint(out: Out) -> bool:
    """Whether to set up a GitLab endpoint now. Declining leaves a valid,
    endpoint-less config — a host is added later by re-running or by hand."""
    return not out.ask("Configure a GitLab endpoint now?", "Y").strip().lower().startswith("n")


def _prompt_gitlab_tokens(
    secrets_dir: Path,
    host: str,
    want_write: bool,
    args: argparse.Namespace,
    out: Out,
) -> None:
    """Prompt the read token (always) and the write token (when write access is
    wanted), upserting each into the grouped host-keyed token file."""
    _ensure_secret(secrets_dir, "read_tokens")
    _ensure_secret(secrets_dir, "write_tokens")

    current = "" if args.force else _read_grouped_token(secrets_dir, "read_tokens", host)
    read_tok = out.secret("GitLab READ token (read_api, read_repository)", current=current)
    if read_tok:
        _upsert_grouped_token(secrets_dir, "read_tokens", host, read_tok)
    else:
        out.warn(f"no read token for {host} — doctor will flag it")

    if want_write:
        current = "" if args.force else _read_grouped_token(secrets_dir, "write_tokens", host)
        write_tok = out.secret("GitLab WRITE token (api scope)", current=current)
        if write_tok:
            _upsert_grouped_token(secrets_dir, "write_tokens", host, write_tok)
        else:
            out.warn(f"no write token for {host} — doctor will flag it")


def _prompt_allowed_projects(
    root: Path,
    warden_toml: Path,
    env_path: Path,
    host: str,
    args: argparse.Namespace,
    out: Out,
) -> None:
    cur_proj, _ = _resolve_allowed_projects(root)
    if cur_proj and not args.force:
        out.info(f"\n  allowed projects already set: {', '.join(cur_proj)} — keeping.")
        return
    print()
    out.info("  Which GitLab project(s) may the agent touch? Full path(s),")
    out.info("  e.g. group/sub/project — comma-separated, no wildcards.")
    # Offer (never silently add) any GitLab remotes found under the init folder as the
    # default — the user accepts with Enter, or edits/clears to decline.
    discovered = _discover_gitlab_projects(root, f"https://{host}")
    default = ", ".join(discovered) if discovered else ""
    if discovered:
        out.info("  Detected GitLab project(s) from git remotes: " + ", ".join(discovered))
    raw = out.ask("projects (group/sub/project,...)", default)
    projects = [p.strip() for p in raw.split(",") if p.strip()]
    valid: list[str] = []
    for p in projects:
        reason = validate_project(p)
        if reason:
            out.warn(f"skipping {p!r}: {reason}")
        else:
            valid.append(p)
    if valid and warden_toml.exists():
        set_toml_list(warden_toml, "allowed_projects", valid)
        out.info(f"  • wrote {len(valid)} project(s) to warden.toml")
    elif not valid:
        out.warn(
            "no projects allowed yet — the stack still starts (you can "
            "work offline), but every GitLab op is denied until you add a "
            "project to allowed_projects in .catraz/config/warden.toml"
        )
    unset_env_keys(env_path, ["WARDEN_ALLOWED_PROJECTS"])


def _prompt_branch_prefix(warden_toml: Path, env_path: Path, out: Out) -> None:
    cur_prefix = _read_branch_prefix(warden_toml)
    prefix = out.ask("Branch prefix the agent may push to", cur_prefix or "claude/")
    if warden_toml.exists():
        set_toml_list(warden_toml, "branch_prefixes", [prefix])
        # Retire the scalar key so it can't coexist with the list we just
        # wrote (Config aborts on both being set — one source of truth).
        remove_toml_key(warden_toml, "branch_prefix")
    unset_env_keys(env_path, ["WARDEN_BRANCH_PREFIX"])


def _prompt_secret_keep_or_replace(secrets_dir: Path, filename: str, label: str, out: Out) -> None:
    """Offer "keep inherited (hidden)" / "enter new" without ever echoing the value."""
    import getpass

    out.info(f"  {label} — inherited (hidden); Enter to keep, or type a new value.")
    try:
        val = getpass.getpass(f"  {label}: ").strip()
    except EOFError:
        val = ""
    if val:
        _write_secret_value(secrets_dir, filename, val)


def _prompt_anthropic_key(
    secrets_dir: Path,
    args: argparse.Namespace,
    out: Out,
    inherited: dict[str, Any] | None = None,
) -> None:
    has_from = inherited is not None
    p_key = secrets_dir / "anthropic_api_key"
    if has_from and p_key.exists():
        _prompt_secret_keep_or_replace(
            secrets_dir,
            "anthropic_api_key",
            "Anthropic API key (dedicated sandbox account)",
            out,
        )
        return
    existing_key = ""
    if p_key.exists() and not args.force:
        try:
            existing_key = p_key.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    val = out.secret(
        "Anthropic API key (dedicated sandbox account, not your primary)",
        current=existing_key,
    )
    _write_secret_value(secrets_dir, "anthropic_api_key", val)
    if not val:
        out.warn("anthropic_api_key left empty — doctor will flag it")


def _wizard_interactive(
    root: Path,
    env: dict[str, str],
    env_path: Path,
    secrets_dir: Path,
    warden_toml: Path,
    updates: dict[str, str],
    args: argparse.Namespace,
    out: Out,
    inherited: dict[str, Any] | None = None,
) -> None:
    """Interactive wizard: each question offers a sensible default via one
    Enter. When *inherited* is not None (--from mode), inherited values take
    precedence over local ones for defaults, and already-set values are
    re-prompted (like --force)."""
    print()

    auth_mode = _prompt_auth_mode(env, args, out, inherited)
    updates["AUTH_MODE"] = auth_mode

    host = ""
    access = "none"
    if _prompt_configure_endpoint(out):
        host = normalize_host(
            out.ask("GitLab host", _read_endpoint_host(warden_toml) or "gitlab.com")
        )
        want_write = _prompt_write_access(out)
        _prompt_gitlab_tokens(secrets_dir, host, want_write, args, out)
        if warden_toml.exists():
            ensure_git_endpoint(warden_toml, host, "gitlab")
        _prompt_allowed_projects(root, warden_toml, env_path, host, args, out)
        _prompt_branch_prefix(warden_toml, env_path, out)
        access = "read-write" if want_write else "read-only"
    else:
        # No endpoint: leave both grouped token files present but empty.
        _ensure_secret(secrets_dir, "read_tokens")
        _ensure_secret(secrets_dir, "write_tokens")

    if auth_mode == "api_key":
        _prompt_anthropic_key(secrets_dir, args, out, inherited)

    proj_count, _ = _resolve_allowed_projects(root)
    endpoint_part = (
        f"  host={host}  access={access}  projects={len(proj_count)}"
        if host
        else ("  gitlab=not configured")
    )
    out.info(
        f"\n• auth_mode={auth_mode}{endpoint_part}  (edit quotas in .catraz/config/warden.toml)"
    )
    out.info("  To change the base image, edit .catraz/config/image/Dockerfile")
