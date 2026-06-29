"""Setup and maintenance commands: init, doctor, sync."""
import argparse
import json
import os
import shutil
from pathlib import Path

from catraz.doctor import _doctor_fix, print_findings, run_doctor
from catraz.envfile import load_env, set_env_values
from catraz.errors import CliError, EXIT_CONFIG, EXIT_DOCTOR, EXIT_OK
from catraz.ui import Out

from ._secrets import _ensure_secret, _write_secret_value  # noqa: F401
from ._sync import _auto_sync_if_needed, _ensure_gitignore, _run_sync  # noqa: F401
from ._wizard_interactive import _wizard_interactive
from ._wizard_yes import _wizard_yes, _yes_gitlab_mode  # noqa: F401

__all__ = [
    "cmd_doctor",
    "cmd_init",
    "cmd_sync",
    "cmd_allow",
    "_auto_sync_if_needed",
    "_ensure_gitignore",
    "_run_sync",
    "_ensure_secret",
    "_write_secret_value",
    "_wizard_interactive",
    "_wizard_yes",
    "_yes_gitlab_mode",
]


def cmd_doctor(root: Path, args: argparse.Namespace, out: Out) -> int:
    from catraz.doctor import DOCTOR_SECTIONS  # noqa: F401
    only = [args.section] if args.section else None
    f = run_doctor(root, only=only, fix=args.fix)
    bad, warn = print_findings(f, out)
    if bad:
        return EXIT_DOCTOR
    if warn and args.strict:
        out.warn("--strict: warnings count as failures")
        return EXIT_DOCTOR
    return EXIT_OK


def _init_config_templates(cat: Path, assets: Path, out: Out) -> None:
    cfg_dst = cat / "config"
    cfg_src = assets / "config"
    for name in ("warden.toml", "allowlist.txt", "squid.conf"):
        src = cfg_src / name
        dst = cfg_dst / name
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
            out.info(f"• copied {name} to .catraz/config/")
    # Seed the user-owned base Dockerfile (default FROM ubuntu:24.04).
    img_dst = cfg_dst / "image"
    img_dst.mkdir(parents=True, exist_ok=True)
    df_dst = img_dst / "Dockerfile"
    df_src = assets / "image" / "Dockerfile"
    if df_src.exists() and not df_dst.exists():
        shutil.copy2(df_src, df_dst)
        out.info("• created .catraz/config/image/Dockerfile (edit to change the base)")


def _init_seed_env(
    cat: Path, assets: Path, env_path: Path, out: Out
) -> tuple[dict[str, str], dict[str, str]]:
    if not env_path.exists():
        example = assets / ".env.example"
        if not example.exists():
            raise CliError(".env.example missing — cannot seed .env", EXIT_CONFIG)
        shutil.copy2(example, env_path)
        out.info("• created .catraz/.env from .env.example")
    env = load_env(env_path)
    updates: dict[str, str] = {}
    if env.get("DEV_UID") != str(os.getuid()):
        updates["DEV_UID"] = str(os.getuid())
    return env, updates


def _init_sync_credentials(
    root: Path, env_path: Path, args: argparse.Namespace, out: Out
) -> None:
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
        ch = claude_home(root)
        ch.mkdir(parents=True, exist_ok=True)
        cj = ch / ".claude.json"
        if not cj.exists():
            cj.write_text(json.dumps(
                {"hasCompletedOnboarding": True, "lastOnboardingVersion": "1.0"}, indent=2))
        out.info("• api_key mode: provisioned default .claude.json")


def _init_preflight(root: Path, out: Out) -> int:
    out.head("\n— preflight —")
    f = run_doctor(root)
    bad, _ = print_findings(f, out)
    print()
    if bad:
        out.info(out.yellow("Some checks failed above. Fix them, then:") + "  catraz doctor")
        return EXIT_DOCTOR
    out.info(out.green("Ready.") + " Next:  " + out.bold("catraz run"))
    return EXIT_OK


def cmd_init(root: Path, args: argparse.Namespace, out: Out) -> int:
    from catraz.paths import asset_root
    from ._from import load_inherited, stage_inherited
    out.head("catraz init — let's get the stack ready\n")
    cat = root / ".catraz"
    env_path = cat / ".env"
    assets = asset_root() / "assets"

    # --from: validate source and load inheritable state before creating dirs.
    init_from: str | None = getattr(args, "init_from", None)
    inherited = None
    if init_from:
        from pathlib import Path as _P
        src_root = _P(init_from).resolve()
        inherited = load_inherited(src_root)  # raises CliError if invalid
        out.info(f"• inheriting from {src_root}/.catraz")

    out.info("• creating .catraz/ directories…")
    _doctor_fix(root, load_env(env_path))

    # Seed README.md tier guide once (never overwrite user edits).
    readme_dst = cat / "README.md"
    if not readme_dst.exists():
        readme_src = assets / "catraz-README.md"
        if readme_src.exists():
            shutil.copy2(readme_src, readme_dst)
            out.info("• created .catraz/README.md (tier guide)")

    # Stage inherited config/ and secrets/ BEFORE seeding the default templates, so an
    # inherited Dockerfile / warden.toml / allowlist / squid.conf wins. _init_config_templates
    # only copies a default when the file is absent (`not dst.exists()`), so it now fills just
    # the gaps the source did not provide instead of pre-empting the inherited copy (in
    # interactive mode stage_inherited skips existing files, so order — not its `yes` flag —
    # is what made the inherited config get dropped). Also lets the wizard read staged values
    # as defaults.
    if inherited:
        stage_inherited(cat, inherited, yes=args.yes, out=out)

    _init_config_templates(cat, assets, out)

    env, updates = _init_seed_env(cat, assets, env_path, out)
    # Overlay inherited .env keys as defaults (local .env takes precedence for DEV_UID).
    if inherited:
        for k, v in inherited.get("env", {}).items():
            if k not in env:
                env[k] = v

    secrets_dir = cat / "secrets"
    secrets_dir.mkdir(mode=0o700, exist_ok=True)
    warden_toml = cat / "config" / "warden.toml"

    if args.yes:
        _wizard_yes(env, env_path, secrets_dir, warden_toml, updates, out, inherited)
    else:
        _wizard_interactive(root, env, env_path, secrets_dir, warden_toml, updates, args, out, inherited)

    if updates:
        set_env_values(env_path, updates)
        out.info(f"\n• wrote {len(updates)} value(s) to .env")

    _init_sync_credentials(root, env_path, args, out)
    _ensure_gitignore(root)
    return _init_preflight(root, out)


def cmd_sync(root: Path, args: argparse.Namespace, out: Out) -> int:
    try:
        _run_sync(root, out, source=args.source, force=args.force)
    except CliError as e:
        out.err(str(e))
        return e.code
    return EXIT_OK


def cmd_allow(root: Path, args: argparse.Namespace, out: Out) -> int:
    from catraz.policy import (
        _read_toml_allowed_projects,
        _resolve_allowed_projects,
        merge_allowed,
        set_toml_list,
        validate_project,
    )
    warden_toml = root / ".catraz" / "config" / "warden.toml"
    if not warden_toml.exists():
        raise CliError("not set up — run catraz init", EXIT_CONFIG)

    valid: list[str] = []
    for p in args.projects:
        reason = validate_project(p)
        if reason:
            out.warn(f"skipping {p!r}: {reason}")
        else:
            valid.append(p)
    if not valid:
        out.err("nothing to add")
        return EXIT_CONFIG

    existing = _read_toml_allowed_projects(warden_toml)
    merged = merge_allowed(existing, valid)
    if merged == [x for x in existing if x]:
        out.info("already allowed — nothing to add")
        return EXIT_OK

    set_toml_list(warden_toml, "allowed_projects", merged)
    out.info(out.green(f"• allowed_projects now: {', '.join(merged)}"))

    if _resolve_allowed_projects(root, load_env(root / ".catraz" / ".env"))[1] == ".env override":
        out.warn("the WARDEN_ALLOWED_PROJECTS override (env or .env) currently shadows "
                 "warden.toml — this change won't take effect until that var is cleared")
    out.info("run `catraz reload` to apply to a running stack")
    return EXIT_OK
