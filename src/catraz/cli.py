"""
catraz — the front door for the claudecatraz stack.

One binary over the 4-step setup ritual (dirs + chown, credential sync, .env,
docker compose). The two stars are `init` (interactive setup session) and
`doctor` (preflight that turns silent failures loud).

Design: docs/design/agentic-workflow/04-cli.md
Pure Python standard library — no install step needed (Docker is the only real
dependency). The CLI is a thin layer over `docker compose`; it never holds
secrets and only ever *writes* `.env` (never config/, never TOML).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from catraz import __version__
from catraz.errors import (
    CliError, EXIT_OK, EXIT_GENERAL, EXIT_CONFIG, EXIT_DOCTOR, EXIT_DOCKER,
)
from catraz.doctor import DOCTOR_SECTIONS
from catraz.ui import Out as Out  # also re-exported for tests that access cli.Out
from catraz import image as _image_mod  # noqa: F401 (kept for tests that import catraz.image)
from catraz.commands import setup, stack, observe
from catraz.commands import run as run_cmd
from catraz.commands import reload as reload_cmd

# ── re-exports (keep these importable from catraz.cli for test back-compat) ─────
# Pure imports keep working: from catraz.cli import X. The redundant `X as X` form
# marks these as explicit re-exports so mypy --strict (no_implicit_reexport) allows
# tests to access them via cli.*.
from catraz.errors import CliError as CliError  # noqa: F811 (already imported above, explicit re-export)
from catraz.commands.setup import _ensure_gitignore as _ensure_gitignore, _run_sync as _run_sync  # noqa: F401
from catraz.commands.run import _oneoff_args as _oneoff_args  # noqa: F401
from catraz.commands.observe import _UdsProxy as _UdsProxy  # noqa: F401
# Compose helpers re-exported for tests that access them via cli.*
from catraz.compose import run as compose_run  # noqa: F401
from catraz.compose import compose_ps as compose_ps  # noqa: F401
from catraz.compose import assert_real_dirs as assert_real_dirs, assert_invariants as assert_invariants  # noqa: F401
from catraz.doctor import run_doctor as run_doctor, print_findings as print_findings  # noqa: F401
from catraz import auth as auth  # noqa: F401
from catraz.commands.stack import _wait_healthy as _wait_healthy, _print_urls as _print_urls  # noqa: F401


# ── commands that stay in cli.py (< 7 lines, no module worth making) ────────────

def cmd_version(root: Path | None, args: argparse.Namespace, out: Out) -> int:
    print(f"catraz {__version__}")
    return EXIT_OK


# ── argument parsing ─────────────────────────────────────────────────────────────

def _g() -> argparse.ArgumentParser:
    """A parent parser carrying the global flags, for subparsers to inherit."""
    parent = argparse.ArgumentParser(add_help=False)
    add_global(parent)
    return parent


def add_global(parser: argparse.ArgumentParser) -> None:
    """Truly global flags — repeated on top parser and every subparser so they work
    before *or* after the subcommand. Command-specific flags (--dry-run, --yes) live
    on their own subparser instead, so they appear only where they actually act."""
    parser.add_argument("-C", "--dir", default=argparse.SUPPRESS,
                        help="project root (default: dir with .catraz/)")
    parser.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS,
                        help="disable ANSI colors")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="catraz",
        description="Front door for the claudecatraz stack. Start with `catraz init`.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_global(p)
    p.add_argument("-V", "--version", action="store_true", help="show version and exit")
    sub = p.add_subparsers(dest="command")

    pi = sub.add_parser("init", parents=[_g()], help="interactive setup session (the wizard)")
    pi.add_argument("--force", action="store_true", help="re-prompt even for set values")
    pi.add_argument("--skip-sync", action="store_true", help="skip the Claude credential import")
    pi.add_argument("-y", "--yes", action="store_true",
                    help="non-interactive; keep existing .env values, skip prompts")

    pd = sub.add_parser("doctor", parents=[_g()], help="preflight: turn silent setup failures loud")
    pd.add_argument("--fix", action="store_true", help="repair safe findings (dirs, chown)")
    pd.add_argument("--strict", action="store_true", help="warnings count as failures (exit 3)")
    pd.add_argument("--section", choices=DOCTOR_SECTIONS, help="run only one section")

    pst = sub.add_parser("stop", aliases=["down"], parents=[_g()], help="stop the stack")
    pst.add_argument("-v", "--volumes", action="store_true", help="also remove volumes")
    pst.add_argument("--print", "--dry-run", dest="print_only", action="store_true",
                     help="show the compose command without running it")

    p_run = sub.add_parser(
        "run",
        help="run the sandbox: run [claude|claude-remote|shell] -- <args> (default mode: claude)")
    p_run.add_argument("claude_args", nargs=argparse.REMAINDER)

    sub.add_parser("status", parents=[_g()], help="health per service, URLs, quota snapshot")

    sub.add_parser("ps", parents=[_g()], help="list active agent containers for this repo")

    pr = sub.add_parser("reload", parents=[_g()],
                        help="restart services whose .catraz config changed")
    pr.add_argument("--force", action="store_true",
                    help="rebuild + (re)start all infra even if not stale or not running")
    pr.add_argument("--print", "--dry-run", dest="print_only", action="store_true",
                    help="show the compose command without running it")

    pl = sub.add_parser("logs", parents=[_g()], help="tail logs (agent|warden|proxy, or --audit)")
    pl.add_argument("service", nargs="?", help="agent | warden | proxy")
    pl.add_argument("-f", "--follow", action="store_true", help="follow")
    pl.add_argument("--tail", type=int, default=100, help="last N lines (default 100)")
    pl.add_argument("--audit", action="store_true", help="warden decision log instead of stdout")

    ps = sub.add_parser("sync", parents=[_g()], help="re-import Claude sandbox credentials from the host")
    ps.add_argument("--from", dest="source", help="source ~/.claude path")
    ps.add_argument("--force", action="store_true", help="overwrite existing credential")

    pal = sub.add_parser("allow", parents=[_g()],
                         help="add GitLab project(s) to the warden allowlist")
    pal.add_argument("projects", nargs="+",
                     help="full project path(s), e.g. group/sub/project")

    pa = sub.add_parser("audit", parents=[_g()], help="warden decision log (JSONL tail or --web viewer)")
    pa.add_argument("--web", action="store_true", help="open the live viewer over the admin socket")
    pa.add_argument("-f", "--follow", action="store_true", help="follow")
    pa.add_argument("--tail", type=int, default=100, help="last N lines (default 100)")

    sub.add_parser("version", parents=[_g()], help="show CLI version")
    return p


HANDLERS = {
    "init":    setup.cmd_init,
    "doctor":  setup.cmd_doctor,
    "stop":    stack.cmd_down,   # canonical
    "down":    stack.cmd_down,   # back-compat alias (argparse aliases=["down"])
    "status":  stack.cmd_status,
    "ps":      observe.cmd_ps,
    "reload":  reload_cmd.cmd_reload,
    "run":     run_cmd.cmd_run,
    "logs":    observe.cmd_logs,
    "audit":   observe.cmd_audit,
    "sync":    setup.cmd_sync,
    "allow":   setup.cmd_allow,
    "version": cmd_version,
}


def main(argv: list[str] | None = None) -> int:
    from catraz.paths import find_root
    parser = build_parser()
    args = parser.parse_args(argv)
    # Normalize SUPPRESS'd global flags so they read uniformly regardless of position.
    args.dir = getattr(args, "dir", None)
    args.print_only = getattr(args, "print_only", False)
    args.yes = getattr(args, "yes", False)
    args.no_color = getattr(args, "no_color", False)
    out = Out(color=not args.no_color)

    if args.version:
        root = None
        try:
            root = find_root(args.dir)
        except CliError:
            pass
        return cmd_version(root, args, out)

    if not args.command:
        parser.print_help()
        return EXIT_OK

    # init runs BEFORE a .catraz exists → it takes the explicit dir (or CWD)
    # as root rather than walking up for an existing .catraz.
    if args.command == "init":
        root = Path(args.dir).resolve() if args.dir else Path.cwd().resolve()
        try:
            return HANDLERS["init"](root, args, out)
        except CliError as e:
            out.err(str(e))
            return e.code
        except KeyboardInterrupt:
            print()
            return EXIT_GENERAL

    try:
        root = find_root(args.dir)
    except CliError as e:
        out.err(str(e))
        return e.code

    try:
        return HANDLERS[args.command](root, args, out)
    except CliError as e:
        out.err(str(e))
        return e.code
    except KeyboardInterrupt:
        print()
        return EXIT_GENERAL


if __name__ == "__main__":
    sys.exit(main())
