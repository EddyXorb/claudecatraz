import argparse
import re
from pathlib import Path
from typing import Any, cast

from catraz.envfile import load_env, unset_env_keys
from catraz.policy import (
    _discover_gitlab_projects,
    _resolve_allowed_projects,
    remove_toml_key,
    set_toml_list,
    validate_project,
)
from catraz.ui import Out

from ._secrets import _ensure_secret, _write_secret_value
from ._wizard_yes import _VALID_GITLAB_MODES


def _read_branch_prefix(env: dict[str, str], warden_toml: Path | None) -> str:
    """Current (first) branch prefix, shown as the wizard's default.

    env override wins (first CSV entry), else warden.toml — either the new list
    form (``branch_prefixes = [...]``) or the legacy scalar (``branch_prefix =
    "..."``); the wizard only ever prompts for one, but must read whichever form
    is on disk so the default reflects reality.
    """
    ov = env.get("WARDEN_BRANCH_PREFIX", "").strip()
    if ov:
        return ov.split(",")[0].strip()
    if not warden_toml or not warden_toml.exists():
        return "claude/"
    text = warden_toml.read_text(encoding="utf-8")
    m = re.search(r'branch_prefixes\s*=\s*\[\s*"([^"]*)"', text)
    if m:
        return m.group(1)
    m = re.search(r'branch_prefix\s*=\s*"([^"]*)"', text)
    return m.group(1) if m else "claude/"


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


def _prompt_gitlab_mode(
    env: dict[str, str],
    out: Out,
    inherited: dict[str, Any] | None = None,
) -> str:
    inh = _inh_env(inherited, "GITLAB_MODE")
    cur_mode = (inh or env.get("GITLAB_MODE") or "read-write").strip()
    if cur_mode not in _VALID_GITLAB_MODES:
        cur_mode = "read-write"
    return cast(
        str,
        out.choice(
            "GitLab integration?",
            [
                ("read-write", "read-write — read + push (needs read & write tokens)"),
                ("read-only", "read-only — read only (needs a read token)"),
                ("off", "off — no GitLab (the agent can't talk to GitLab)"),
            ],
            default={"read-write": 0, "read-only": 1, "off": 2}[cur_mode],
        ),
    )


def _prompt_gitlab_tokens(
    secrets_dir: Path,
    mode: str,
    args: argparse.Namespace,
    out: Out,
    inherited: dict[str, Any] | None = None,
) -> None:
    has_from = inherited is not None
    p_read = secrets_dir / "gitlab_read_token"
    # With --from: the file was staged; offer keep-inherited without echoing.
    if has_from and p_read.exists():
        _prompt_secret_keep_or_replace(
            secrets_dir,
            "gitlab_read_token",
            "GitLab READ token (read_api, read_repository)",
            out,
        )
    else:
        existing_read = ""
        if p_read.exists() and not args.force:
            try:
                existing_read = p_read.read_text(encoding="utf-8").strip()
            except OSError:
                pass
        val = out.secret(
            "GitLab READ token (read_api, read_repository)", current=existing_read
        )
        _write_secret_value(secrets_dir, "gitlab_read_token", val)
        if not val:
            out.warn("gitlab_read_token left empty — doctor will flag it")

    if mode == "read-write":
        p_write = secrets_dir / "gitlab_write_token"
        if has_from and p_write.exists():
            _prompt_secret_keep_or_replace(
                secrets_dir, "gitlab_write_token", "GitLab WRITE token (api scope)", out
            )
        else:
            existing_write = ""
            if p_write.exists() and not args.force:
                try:
                    existing_write = p_write.read_text(encoding="utf-8").strip()
                except OSError:
                    pass
            val = out.secret("GitLab WRITE token (api scope)", current=existing_write)
            _write_secret_value(secrets_dir, "gitlab_write_token", val)
            if not val:
                out.warn("gitlab_write_token left empty — doctor will flag it")
    else:
        _ensure_secret(secrets_dir, "gitlab_write_token")


def _prompt_secret_keep_or_replace(
    secrets_dir: Path, filename: str, label: str, out: Out
) -> None:
    """Offer "keep inherited (hidden)" / "enter new" without ever echoing the value."""
    import getpass

    out.info(f"  {label} — inherited (hidden); Enter to keep, or type a new value.")
    try:
        val = getpass.getpass(f"  {label}: ").strip()
    except EOFError:
        val = ""
    if val:
        _write_secret_value(secrets_dir, filename, val)


def _prompt_allowed_projects(
    root: Path,
    env: dict[str, str],
    warden_toml: Path,
    gitlab_url: str,
    args: argparse.Namespace,
    out: Out,
) -> None:
    cur_proj, _ = _resolve_allowed_projects(root, env)
    if cur_proj and not args.force:
        out.info(f"\n  allowed projects already set: {', '.join(cur_proj)} — keeping.")
        return
    print()
    out.info("  Which GitLab project(s) may the agent touch? Full path(s),")
    out.info("  e.g. group/sub/project — comma-separated, no wildcards.")
    # Offer (never silently add) any GitLab remotes found under the init folder as the
    # default — the user accepts with Enter, or edits/clears to decline.
    discovered = _discover_gitlab_projects(root, gitlab_url)
    default = ", ".join(discovered) if discovered else ""
    if discovered:
        out.info(
            "  Detected GitLab project(s) from git remotes: " + ", ".join(discovered)
        )
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


def _prompt_branch_prefix(
    env: dict[str, str], warden_toml: Path, env_path: Path, out: Out
) -> None:
    cur_prefix = _read_branch_prefix(env, warden_toml)
    prefix = out.ask("Branch prefix the agent may push to", cur_prefix or "claude/")
    if warden_toml.exists():
        set_toml_list(warden_toml, "branch_prefixes", [prefix])
        # Retire the legacy scalar key so it can't coexist with the list we just
        # wrote (Config aborts on both being set — one source of truth).
        remove_toml_key(warden_toml, "branch_prefix")
    unset_env_keys(env_path, ["WARDEN_ALLOWED_PROJECTS", "WARDEN_BRANCH_PREFIX"])


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
    """Interactive wizard: each question offers a sensible default via one Enter.

    When *inherited* is not None (--from mode), inherited values take precedence
    over locally set values for defaults, and already-set values are re-prompted
    (like --force).
    """
    print()

    auth_mode = _prompt_auth_mode(env, args, out, inherited)
    updates["AUTH_MODE"] = auth_mode

    mode = _prompt_gitlab_mode(env, out, inherited)
    updates["GITLAB_MODE"] = mode

    if mode != "off":
        inh_url = _inh_env(inherited, "GITLAB_URL")
        url = out.ask(
            "GitLab base URL (set for self-hosted)",
            inh_url or env.get("GITLAB_URL") or "https://gitlab.com",
        )
        updates["GITLAB_URL"] = url
        _prompt_gitlab_tokens(secrets_dir, mode, args, out, inherited)
        _prompt_allowed_projects(root, env, warden_toml, url, args, out)
        _prompt_branch_prefix(env, warden_toml, env_path, out)
    else:
        _ensure_secret(secrets_dir, "gitlab_read_token")
        _ensure_secret(secrets_dir, "gitlab_write_token")

    if auth_mode == "api_key":
        _prompt_anthropic_key(secrets_dir, args, out, inherited)

    proj_count, _ = _resolve_allowed_projects(root, load_env(env_path))
    url_part = (
        f"  url={updates.get('GITLAB_URL', env.get('GITLAB_URL', ''))}"
        if mode != "off"
        else ""
    )
    proj_part = f"  projects={len(proj_count)}" if mode != "off" else ""
    out.info(
        f"\n• auth_mode={auth_mode}  gitlab_mode={mode}"
        f"{url_part}{proj_part}"
        "  (edit quotas in .catraz/config/warden.toml)"
    )
    out.info("  To change the base image, edit .catraz/config/image/Dockerfile")
