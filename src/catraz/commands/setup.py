"""Setup and maintenance commands: init, doctor, sync."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from catraz.errors import CliError, EXIT_OK, EXIT_GENERAL, EXIT_CONFIG, EXIT_DOCTOR
from catraz.envfile import load_env, set_env_values, unset_env_keys
from catraz.policy import (
    validate_project, _resolve_allowed_projects,
    set_toml_scalar, set_toml_list,
)
from catraz.doctor import run_doctor, print_findings, _doctor_fix, SECRETS


# Valid GITLAB_MODE values (used in both --yes inference and interactive prompt).
_VALID_GITLAB_MODES = ("off", "read-only", "read-write")


def cmd_doctor(root, args, out):
    from catraz.doctor import DOCTOR_SECTIONS
    only = [args.section] if args.section else None
    f = run_doctor(root, only=only, fix=args.fix)
    bad, warn = print_findings(f, out)
    if bad:
        return EXIT_DOCTOR
    if warn and args.strict:
        out.warn("--strict: warnings count as failures")
        return EXIT_DOCTOR
    return EXIT_OK


def cmd_init(root, args, out):
    from catraz.paths import asset_root
    out.head("catraz init — let's get the stack ready\n")
    cat = root / ".catraz"
    env_path = cat / ".env"
    assets = asset_root() / "assets"

    # 1. dirs (.catraz/ + subdirs, chown DEV_UID)
    out.info("• creating .catraz/ directories…")
    _doctor_fix(root, load_env(env_path))  # mkdir under .catraz/ + best-effort chown

    # 2. config templates → .catraz/config/ (only if not already present)
    cfg_dst = cat / "config"
    cfg_src = assets / "config"
    for name in ("warden.toml", "allowlist.txt", "squid.conf"):
        src = cfg_src / name
        dst = cfg_dst / name
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
            out.info(f"• copied {name} to .catraz/config/")

    # 3. .catraz/.env seeded from the packaged .env.example
    if not env_path.exists():
        example = assets / ".env.example"
        if not example.exists():
            raise CliError(".env.example missing — cannot seed .env", EXIT_CONFIG)
        shutil.copy2(example, env_path)
        out.info("• created .catraz/.env from .env.example")
    env = load_env(env_path)

    updates = {}
    # DEV_UID → current user, so bind-mount ownership lines up by default.
    if env.get("DEV_UID") != str(os.getuid()):
        updates["DEV_UID"] = str(os.getuid())

    # Ensure secrets dir exists (compose mounts fail opaquely without it).
    secrets_dir = cat / "secrets"
    secrets_dir.mkdir(mode=0o700, exist_ok=True)
    warden_toml = cat / "config" / "warden.toml"

    # Wizard: populate updates + write secret files / warden.toml policy.
    if args.yes:
        _wizard_yes(env, env_path, secrets_dir, warden_toml, updates, out)
    else:
        _wizard_interactive(root, env, env_path, secrets_dir, warden_toml,
                            updates, args, out)

    if updates:
        set_env_values(env_path, updates)
        out.info(f"\n• wrote {len(updates)} value(s) to .env")

    # 5. sync — provision .claude.json no matter the auth mode
    from catraz.paths import claude_home
    auth_mode = load_env(env_path).get("AUTH_MODE", "subscription")
    if args.skip_sync:
        out.info("• --skip-sync: skipping Claude credential import")
    elif auth_mode == "subscription":
        out.info("\n• importing Claude credentials (sync)…")
        try:
            _run_sync(root, out)
        except CliError as e:
            out.warn(str(e) + " — run `catraz sync` once authenticated")
    else:
        # api_key mode: no subscription credential to sync, but the subscription
        # RO-bind still targets .catraz/claude/.claude.json — always provision it.
        ch = claude_home(root)
        ch.mkdir(parents=True, exist_ok=True)
        cj = ch / ".claude.json"
        if not cj.exists():
            cj.write_text(json.dumps(
                {"hasCompletedOnboarding": True, "lastOnboardingVersion": "1.0"}, indent=2))
        out.info("• api_key mode: provisioned default .claude.json")

    # 6. .gitignore — keep the runtime/secrets home out of version control
    _ensure_gitignore(root)

    # 7. doctor
    out.head("\n— preflight —")
    f = run_doctor(root)
    bad, _ = print_findings(f, out)
    print()
    if bad:
        out.info(out.yellow("Some checks failed above. Fix them, then:") + "  catraz doctor")
        return EXIT_DOCTOR
    out.info(out.green("Ready.") + " Next:  " + out.bold("catraz up"))
    return EXIT_OK


# ---------------------------------------------------------------------------
# Secret-file helpers (shared by both wizard paths)
# ---------------------------------------------------------------------------

def _ensure_secret(secrets_dir, filename):
    """Ensure secret file exists at 0600.  Never overwrites non-empty existing content."""
    p = secrets_dir / filename
    if not p.exists():
        p.write_text("")
        p.chmod(0o600)


def _write_secret_value(secrets_dir, filename, value):
    """Write *value* to secrets_dir/filename at 0600.  Creates the file if missing."""
    p = secrets_dir / filename
    p.write_text(value)
    p.chmod(0o600)


# ---------------------------------------------------------------------------
# --yes path
# ---------------------------------------------------------------------------

def _yes_gitlab_mode(env):
    """Determine GITLAB_MODE for --yes: explicit env wins, else infer from tokens."""
    mode = (os.environ.get("GITLAB_MODE") or env.get("GITLAB_MODE") or "").strip()
    if mode in _VALID_GITLAB_MODES:
        return mode
    # Infer from token env vars
    read_t = os.environ.get("GITLAB_READ_TOKEN", "").strip()
    write_t = os.environ.get("GITLAB_WRITE_TOKEN", "").strip()
    if read_t and write_t:
        return "read-write"
    if read_t:
        return "read-only"
    return "off"


def _wizard_yes(env, env_path, secrets_dir, warden_toml, updates, out):
    """Non-interactive wizard path for --yes."""
    out.info("• --yes: skipping prompts")

    # AUTH_MODE
    auth_mode = (os.environ.get("AUTH_MODE") or env.get("AUTH_MODE") or "subscription")
    updates["AUTH_MODE"] = auth_mode

    # GITLAB_MODE
    mode = _yes_gitlab_mode(env)
    updates["GITLAB_MODE"] = mode

    # GITLAB_URL
    gitlab_url = (os.environ.get("GITLAB_URL", "").strip() or env.get("GITLAB_URL", ""))
    if gitlab_url:
        updates["GITLAB_URL"] = gitlab_url

    # Tokens: write from env vars exactly as before (non-destructive for unset vars).
    # Both gitlab token files are always ensured (compose mounts need the files).
    for filename, _, _ in SECRETS:
        p = secrets_dir / filename
        env_val = os.environ.get(filename.upper(), "").strip()
        if env_val:
            p.write_text(env_val)
            p.chmod(0o600)
        elif not p.exists():
            p.write_text("")
            p.chmod(0o600)

    # anthropic_api_key — only in api_key mode
    if auth_mode == "api_key":
        p = secrets_dir / "anthropic_api_key"
        env_val = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if env_val:
            p.write_text(env_val)
            p.chmod(0o600)
        elif not p.exists():
            p.write_text("")
            p.chmod(0o600)

    # Policy: write to warden.toml (not .env) when provided.
    # Always written regardless of mode so that explicit env overrides land
    # in the right SSOT (the user opted in by setting the var).
    raw_projects = (
        os.environ.get("WARDEN_ALLOWED_PROJECTS") or
        env.get("WARDEN_ALLOWED_PROJECTS", "")
    ).strip()
    if raw_projects and warden_toml.exists():
        projects = [p.strip() for p in raw_projects.split(",") if p.strip()]
        valid = []
        for proj in projects:
            reason = validate_project(proj)
            if reason:
                out.warn(f"  WARDEN_ALLOWED_PROJECTS: skipping {proj!r}: {reason}")
            else:
                valid.append(proj)
        if valid:
            set_toml_list(warden_toml, "allowed_projects", valid)
            out.info(f"  • wrote {len(valid)} project(s) to warden.toml")

    raw_prefix = (
        os.environ.get("WARDEN_BRANCH_PREFIX") or
        env.get("WARDEN_BRANCH_PREFIX", "")
    ).strip()
    if raw_prefix and warden_toml.exists():
        set_toml_scalar(warden_toml, "branch_prefix", raw_prefix)

    # Migrate stale .env overrides (old cmd_init wrote WARDEN_* to .env).
    unset_env_keys(env_path, ["WARDEN_ALLOWED_PROJECTS", "WARDEN_BRANCH_PREFIX"])


# ---------------------------------------------------------------------------
# Interactive path
# ---------------------------------------------------------------------------

def _read_branch_prefix(env, warden_toml):
    """Current branch_prefix: env override wins, else read from warden.toml."""
    ov = env.get("WARDEN_BRANCH_PREFIX", "").strip()
    if ov:
        return ov
    if not warden_toml or not warden_toml.exists():
        return "claude/"
    text = warden_toml.read_text(encoding="utf-8")
    m = re.search(r'branch_prefix\s*=\s*"([^"]*)"', text)
    return m.group(1) if m else "claude/"


def _wizard_interactive(root, env, env_path, secrets_dir, warden_toml,
                        updates, args, out):
    """Interactive wizard: each question offers a sensible default via one Enter."""
    print()

    # ── AUTH_MODE ─────────────────────────────────────────────────────────────
    auth_mode = env.get("AUTH_MODE") or "subscription"
    if args.force or "AUTH_MODE" not in env:
        auth_mode = out.choice(
            "Claude auth mode?",
            [("subscription",
              "subscription — import host ~/.claude (default)"),
             ("api_key",
              "api_key — dedicated Anthropic API key")],
            default=0 if auth_mode == "subscription" else 1,
        )
    updates["AUTH_MODE"] = auth_mode

    # ── GITLAB_MODE ────────────────────────────────────────────────────────────
    cur_mode = (env.get("GITLAB_MODE") or "read-write").strip()
    if cur_mode not in _VALID_GITLAB_MODES:
        cur_mode = "read-write"
    mode = out.choice(
        "GitLab integration?",
        [("read-write",
          "read-write — read + push (needs read & write tokens)"),
         ("read-only",
          "read-only — read only (needs a read token)"),
         ("off",
          "off — no GitLab (the agent can't talk to GitLab)")],
        default={"read-write": 0, "read-only": 1, "off": 2}[cur_mode],
    )
    updates["GITLAB_MODE"] = mode

    if mode != "off":
        # ── GITLAB_URL ─────────────────────────────────────────────────────────
        url = out.ask(
            "GitLab base URL (set for self-hosted)",
            env.get("GITLAB_URL") or "https://gitlab.com",
        )
        updates["GITLAB_URL"] = url

        # ── Read token (always for non-off modes) ──────────────────────────────
        existing_read = ""
        p_read = secrets_dir / "gitlab_read_token"
        if p_read.exists() and not args.force:
            try:
                existing_read = p_read.read_text(encoding="utf-8").strip()
            except OSError:
                pass
        val = out.secret(
            "GitLab READ token (read_api, read_repository)",
            current=existing_read,
        )
        _write_secret_value(secrets_dir, "gitlab_read_token", val)
        if not val:
            out.warn("gitlab_read_token left empty — doctor will flag it")

        # ── Write token (read-write only) ─────────────────────────────────────
        if mode == "read-write":
            existing_write = ""
            p_write = secrets_dir / "gitlab_write_token"
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
            # read-only: ensure write token file exists for the compose mount,
            # but never overwrite a non-empty token (user may want it back later).
            _ensure_secret(secrets_dir, "gitlab_write_token")

        # ── Policy ────────────────────────────────────────────────────────────
        cur_proj, _ = _resolve_allowed_projects(root, env)
        if cur_proj and not args.force:
            out.info(f"\n  allowed projects already set: {', '.join(cur_proj)} — keeping.")
        else:
            print()
            out.info("  Which GitLab project(s) may the agent touch? Full path(s),")
            out.info("  e.g. group/sub/project — comma-separated, no wildcards.")
            raw = out.ask("projects (group/sub/project,...)", "")
            projects = [p.strip() for p in raw.split(",") if p.strip()]
            valid = []
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

        # ── Branch prefix ─────────────────────────────────────────────────────
        cur_prefix = _read_branch_prefix(env, warden_toml)
        prefix = out.ask("Branch prefix the agent may push to", cur_prefix or "claude/")
        if warden_toml.exists():
            set_toml_scalar(warden_toml, "branch_prefix", prefix)

        # Migrate any stale WARDEN_* overrides from .env → they shadow warden.toml.
        unset_env_keys(env_path, ["WARDEN_ALLOWED_PROJECTS", "WARDEN_BRANCH_PREFIX"])

    else:
        # off: ensure both token files exist so compose secret mounts don't fail.
        _ensure_secret(secrets_dir, "gitlab_read_token")
        _ensure_secret(secrets_dir, "gitlab_write_token")

    # ── anthropic_api_key (only in api_key mode) ───────────────────────────────
    if auth_mode == "api_key":
        existing_key = ""
        p_key = secrets_dir / "anthropic_api_key"
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

    # ── Summary ───────────────────────────────────────────────────────────────
    proj_count, _ = _resolve_allowed_projects(root, load_env(env_path))
    url_part = (f"  url={updates.get('GITLAB_URL', env.get('GITLAB_URL', ''))}"
                if mode != "off" else "")
    proj_part = f"  projects={len(proj_count)}" if mode != "off" else ""
    out.info(
        f"\n• auth_mode={auth_mode}  gitlab_mode={mode}"
        f"{url_part}{proj_part}"
        "  (edit quotas in .catraz/config/warden.toml)"
    )


# ---------------------------------------------------------------------------
# Remaining setup commands (unchanged)
# ---------------------------------------------------------------------------

def _ensure_gitignore(root):
    """Append a `.catraz/` line to <root>/.gitignore (create if missing), once."""
    gi = root / ".gitignore"
    lines = gi.read_text().splitlines() if gi.exists() else []
    if any(ln.strip() == ".catraz/" for ln in lines):
        return
    with gi.open("a") as fh:
        if lines and lines[-1].strip():
            fh.write("\n")
        fh.write(".catraz/\n")


def _run_sync(root, out, source=None, force=False, quiet=False):
    from catraz.paths import asset_root
    entry = asset_root() / "assets" / "container" / "entrypoint.py"
    if not entry.exists():
        raise CliError(
            "entrypoint.py asset not found (corrupt cache? remove ~/.cache/catraz)",
            EXIT_GENERAL,
        )
    env = load_env(root / ".catraz" / ".env")
    from catraz.paths import claude_home
    home = claude_home(root)
    cmd = [sys.executable, str(entry), "sync", "--claude-home", str(home)]
    src = (source
           or os.environ.get("CLAUDE_CREDENTIAL_SOURCE")
           or env.get("CLAUDE_CREDENTIAL_SOURCE"))
    if src:
        cmd += ["--from", str(Path(src).expanduser())]
    # quiet swallows the entrypoint's "Credentials synced …" line for silent refreshes.
    r = subprocess.run(cmd, cwd=root, env=dict(os.environ), capture_output=quiet, text=True)
    if r.returncode != 0:
        raise CliError("credential sync failed", EXIT_GENERAL)


def cmd_sync(root, args, out):
    try:
        _run_sync(root, out, source=args.source, force=args.force)
    except CliError as e:
        out.err(str(e))
        return e.code
    return EXIT_OK


def _auto_sync_if_needed(root, out):
    """Subscription: keep the sandbox seed credential as fresh as the host (best-effort).

    The host ~/.claude credential advances whenever Claude refreshes it there; the sandbox
    seed is frozen at sync time (in-container home is tmpfs → refreshes never flow back). So
    on every (cold) start we re-copy host→sandbox: a host that's used now and then keeps the
    seed current with no manual `catraz sync`. Strictly one-way; the untrusted agent never
    writes toward the host. Cannot help when BOTH host and seed are dead (needs an interactive
    host `claude` login); does NOT reflect the agent's live tmpfs token — only keeps the seed
    as fresh as the host.
    """
    from catraz.paths import claude_home
    if load_env(root / ".catraz" / ".env").get("AUTH_MODE", "subscription") != "subscription":
        return
    had = (claude_home(root) / ".credentials.json").exists()
    if not had:
        out.info("• subscription credential missing — attempting sync…")
    try:
        _run_sync(root, out, quiet=had)          # refreshing an existing seed is silent
    except CliError as e:
        # Missing seed + failed sync is a real problem (auth fails closed downstream).
        # Refreshing an existing seed is best-effort: a briefly-unreachable host must not
        # nag — the existing seed still works.
        if not had:
            out.warn(str(e) + " — run `catraz sync` once authenticated")
