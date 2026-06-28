"""Setup and maintenance commands: init, doctor, sync."""
import getpass
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from catraz.errors import CliError, EXIT_OK, EXIT_GENERAL, EXIT_CONFIG, EXIT_DOCTOR
from catraz.envfile import load_env, set_env_values, mask
from catraz.policy import validate_project, _resolve_allowed_projects
from catraz.doctor import run_doctor, print_findings, _doctor_fix, SECRETS
from catraz import auth


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

    # 3. secrets — always create dir + files (empty allowed); compose mount fails opaquely otherwise
    secrets_dir = cat / "secrets"
    secrets_dir.mkdir(mode=0o700, exist_ok=True)
    auth_mode = env.get("AUTH_MODE") or "subscription"
    # Build the full secrets list: always GitLab tokens + Anthropic key in api_key mode.
    secret_prompts = list(SECRETS)
    if auth_mode == "api_key":
        secret_prompts.append(("anthropic_api_key",
                                "Anthropic API key (dedicated sandbox account, not your primary)",
                                "Anthropic API key"))

    if args.yes:
        out.info("• --yes: keeping existing .env values, skipping prompts")
        # Secret filenames are the lowercase form of their env var names
        # (e.g. gitlab_read_token <-> GITLAB_READ_TOKEN).
        for filename, _, _desc in secret_prompts:
            path = secrets_dir / filename
            env_val = os.environ.get(filename.upper(), "").strip()
            if env_val:
                path.write_text(env_val)
                path.chmod(0o600)
            elif not path.exists():
                path.write_text("")
                path.chmod(0o600)

        # .env key injection — different storage from secrets above (dict, not files).
        gitlab_url = os.environ.get("GITLAB_URL", "").strip()
        if gitlab_url:
            updates["GITLAB_URL"] = gitlab_url

        raw_projects = os.environ.get("WARDEN_ALLOWED_PROJECTS", "").strip()
        if raw_projects:
            projects = [proj.strip() for proj in raw_projects.split(",") if proj.strip()]
            valid = []
            for proj in projects:
                reason = validate_project(proj)
                if reason:
                    out.warn(f"  WARDEN_ALLOWED_PROJECTS: skipping {proj!r}: {reason}")
                else:
                    valid.append(proj)
            if valid:
                updates["WARDEN_ALLOWED_PROJECTS"] = ",".join(valid)
    else:
        print()
        for filename, prompt, desc in secret_prompts:
            p = secrets_dir / filename
            cur = ""
            if p.exists():
                try:
                    cur = p.read_text(encoding="utf-8").strip()
                except OSError:
                    pass
            if cur and not args.force:
                out.info(f"  {desc} already set ({mask(cur)}) — keeping. Use --force to change.")
                if not p.exists():
                    p.write_text(cur)
                    p.chmod(0o600)
                continue
            val = getpass.getpass(f"  {prompt}\n  {filename}: ").strip()
            p.write_text(val)
            p.chmod(0o600)
            if not val:
                out.warn(f"{filename} left empty — doctor will flag it")

        # 4. GitLab base URL (non-secret, use input() not getpass)
        cur_url = env.get("GITLAB_URL", "")
        default_url = cur_url or "https://gitlab.com"
        print()
        raw_url = input(f"  GitLab base URL (set this for self-hosted GitLab)\n"
                        f"  GITLAB_URL [{default_url}]: ").strip()
        new_url = raw_url or default_url
        if new_url != cur_url:
            updates["GITLAB_URL"] = new_url

        # 5. allowed projects (the roast fix: without this the warden won't start)
        cur_proj, _ = _resolve_allowed_projects(root, env)
        if cur_proj and not args.force:
            out.info(f"\n  allowed projects already set: {', '.join(cur_proj)} — keeping.")
        else:
            print()
            out.info("  Which GitLab project(s) may the agent touch? Full path(s),")
            out.info("  e.g. group/sub/project — comma-separated, no wildcards.")
            raw = input("  projects: ").strip()
            projects = [p.strip() for p in raw.split(",") if p.strip()]
            valid = []
            for p in projects:
                reason = validate_project(p)
                if reason:
                    out.warn(f"skipping {p!r}: {reason}")
                else:
                    valid.append(p)
            if valid:
                updates["WARDEN_ALLOWED_PROJECTS"] = ",".join(valid)

    if updates:
        set_env_values(env_path, updates)
        out.info(f"\n• wrote {len(updates)} value(s) to .env")

    # 5. sync — provision .claude.json no matter the auth mode (so the RO-bind target exists).
    from catraz.paths import claude_home
    mode = load_env(env_path).get("AUTH_MODE", "subscription")
    if args.skip_sync:
        out.info("• --skip-sync: skipping Claude credential import")
    elif mode == "subscription":
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
