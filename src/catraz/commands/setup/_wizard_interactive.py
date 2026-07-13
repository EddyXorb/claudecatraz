import argparse
from pathlib import Path
from typing import Any, cast

from catraz.envfile import unset_env_keys
from catraz.policy import (
    _discover_gitlab_projects,
    _resolve_allowed_projects,
    ensure_git_endpoint,
    first_endpoint_host,
    normalize_host,
    set_endpoint_allowed_projects,
    set_git_rules_branch_prefixes,
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
    """Current (first) `[git.rules].branch_prefixes` entry, shown as the
    wizard's default; falls back to "claude/" when unset."""
    if not warden_toml or not warden_toml.exists():
        return "claude/"
    import tomllib

    try:
        git = tomllib.loads(warden_toml.read_text(encoding="utf-8")).get("git", {})
    except (tomllib.TOMLDecodeError, OSError):
        return "claude/"
    rules = git.get("rules", {}) if isinstance(git, dict) else {}
    prefixes = rules.get("branch_prefixes") if isinstance(rules, dict) else None
    if isinstance(prefixes, list) and prefixes and isinstance(prefixes[0], str):
        return prefixes[0]
    return "claude/"


def _read_endpoint_host(warden_toml: Path | None) -> str | None:
    """First configured `[[git.endpoint]]` host, or None when none is set —
    a fresh repo has no endpoint until the wizard adds one."""
    if not warden_toml or not warden_toml.exists():
        return None
    return first_endpoint_host(warden_toml)


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
                ("subscription", "subscription — use your Claude login (default)"),
                ("api_key", "api_key — dedicated Anthropic API key"),
            ],
            default=0 if auth_mode == "subscription" else 1,
        )
    return auth_mode


def _prompt_credentials_mode(
    env: dict[str, str],
    args: argparse.Namespace,
    out: Out,
    inherited: dict[str, Any] | None = None,
) -> str:
    inh = _inh_env(inherited, "CLAUDE_CREDENTIALS_MODE")
    mode = inh or env.get("CLAUDE_CREDENTIALS_MODE") or "persistent"
    has_from = inherited is not None
    if args.force or "CLAUDE_CREDENTIALS_MODE" not in env or has_from:
        mode = out.choice(
            "Claude credential storage?",
            [
                ("persistent", "persistent — durable login inside the container (default)"),
                ("sync", "sync — read-only import from host ~/.claude, no write-back"),
            ],
            default=0 if mode == "persistent" else 1,
        )
    return mode


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
    cur_proj, _ = _resolve_allowed_projects(root, host)
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
        set_endpoint_allowed_projects(warden_toml, host, valid)
        out.info(f"  • wrote {len(valid)} project(s) to warden.toml")
    elif not valid:
        out.warn(
            "no projects allowed yet — the stack still starts (you can "
            "work offline), but every GitLab op is denied until you add a "
            f"project to allowed_projects on the {host!r} endpoint in "
            ".catraz/config/warden.toml"
        )
    unset_env_keys(env_path, ["WARDEN_ALLOWED_PROJECTS"])


def _prompt_branch_prefix(warden_toml: Path, env_path: Path, out: Out) -> None:
    cur_prefix = _read_branch_prefix(warden_toml)
    prefix = out.ask("Branch prefix the agent may push to", cur_prefix or "claude/")
    if warden_toml.exists():
        set_git_rules_branch_prefixes(warden_toml, [prefix])
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


def _prompt_egress_offer(root: Path, cat: Path, out: Out) -> None:
    """Offer each manifest egress domain not already reachable, one by one, and
    write the accepted ones into the profile's marked allowlist block. Declining
    writes nothing; a domain the operator hand-deleted is offered again, never
    silently restored. Interactive path only — `--yes` never offers or adds."""
    from catraz.agents import SHIPPED_PROFILES, load_manifest, resolve_agent_profile
    from catraz.egress_allowlist import agent_block, domain_covered, upsert_agent_block

    profile = resolve_agent_profile(root)
    manifest = load_manifest(profile)
    allowlist_path = cat / "config" / "allowlist.txt"
    if not manifest.egress_domains or not allowlist_path.exists():
        return
    text = allowlist_path.read_text(encoding="utf-8")
    candidates = [d for d in manifest.egress_domains if not domain_covered(text, d)]
    if not candidates:
        return

    if profile not in SHIPPED_PROFILES:
        # An out-of-tree manifest cannot reach the per-domain offer without the
        # operator first confirming its whole egress set from a shown diff.
        out.info(f"\n  Profile {profile!r} is not shipped — its egress domains would add:")
        for d in candidates:
            out.info(f"    + {d}")
        gate = out.ask(f"review these {len(candidates)} domain(s) for {profile!r}?", "n")
        if not gate.strip().lower().startswith("y"):
            return

    existing = agent_block(text, profile) or ()
    accepted = [
        d for d in candidates if out.ask(f"allow {d}?", "n").strip().lower().startswith("y")
    ]
    confirmed = list(existing) + [d for d in accepted if d not in existing]
    if not accepted and not existing:
        out.info(f"\n• no domains added for profile {profile!r}")
        return
    allowlist_path.write_text(upsert_agent_block(text, profile, tuple(confirmed)))
    out.info(f"\n• {len(accepted)} domain(s) allowed for profile {profile!r}")


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

    creds_mode = _prompt_credentials_mode(env, args, out, inherited)
    updates["CLAUDE_CREDENTIALS_MODE"] = creds_mode

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

    proj_count, _ = _resolve_allowed_projects(root, host) if host else ([], "")
    endpoint_part = (
        f"  host={host}  access={access}  projects={len(proj_count)}"
        if host
        else ("  gitlab=not configured")
    )
    out.info(
        f"\n• auth_mode={auth_mode}{endpoint_part}  (edit quotas in .catraz/config/warden.toml)"
    )
    out.info("  To change the base image, edit .catraz/config/image/Dockerfile")

    _prompt_egress_offer(root, root / ".catraz", out)
