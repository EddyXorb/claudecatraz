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

import argparse
import sys
from pathlib import Path

from catraz import __version__
from catraz.errors import (
    CliError, EXIT_OK, EXIT_GENERAL, EXIT_CONFIG, EXIT_DOCTOR, EXIT_DOCKER,
)
from catraz.doctor import DOCTOR_SECTIONS
from catraz.ui import Out
from catraz import image as _image_mod  # noqa: F401 (kept for tests that import catraz.image)
from catraz.commands import setup, stack, observe
from catraz.commands import run as run_cmd

# ── re-exports (keep these importable from catraz.cli for test back-compat) ─────
# Pure imports keep working: from catraz.cli import X
from catraz.errors import CliError  # noqa: F811 (already imported above, explicit re-export)
from catraz.ui import Out  # noqa: F811
from catraz.commands.setup import _ensure_gitignore, _run_sync  # noqa: F401
from catraz.commands.run import _oneoff_args  # noqa: F401
from catraz.commands.observe import _UdsProxy  # noqa: F401
from catraz.commands.stack import cmd_up  # noqa: F401
# Compose helpers re-exported for tests that access them via cli.*
from catraz.compose import run as compose_run  # noqa: F401
from catraz.compose import compose_ps  # noqa: F401
from catraz.compose import assert_real_dirs, assert_invariants  # noqa: F401
from catraz.doctor import run_doctor, print_findings  # noqa: F401
from catraz import auth  # noqa: F401
from catraz.commands.stack import _wait_healthy, _print_urls  # noqa: F401


# ── commands that stay in cli.py (< 7 lines, no module worth making) ────────────

def cmd_prune(root, args, out):
    from catraz import image
    image.prune()
    out.info(out.green("• removed built catraz-base images"))
    return EXIT_OK


def cmd_version(root, args, out):
    print(f"catraz {__version__}")
    return EXIT_OK


# ── argument parsing ─────────────────────────────────────────────────────────────

def _g():
    """A parent parser carrying the global flags, for subparsers to inherit."""
    parent = argparse.ArgumentParser(add_help=False)
    add_global(parent)
    return parent


def add_global(parser):
    """Truly global flags — repeated on top parser and every subparser so they work
    before *or* after the subcommand. Command-specific flags (--dry-run, --yes) live
    on their own subparser instead, so they appear only where they actually act."""
    parser.add_argument("-C", "--dir", default=argparse.SUPPRESS,
                        help="project root (default: dir with .catraz/)")
    parser.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS,
                        help="disable ANSI colors")


def build_parser():
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

    pu = sub.add_parser("up", parents=[_g()], help="start the stack, wait for health, print URLs")
    pu.add_argument("--build", action="store_true", help="rebuild images first")
    pu.add_argument("--pull", action="store_true", help="pull base images first")
    pu.add_argument("--remote", action="store_true",
                    help="also start the agent daemon (remote-control)")
    pu.add_argument("--no-wait", action="store_true", help="don't wait for health")
    pu.add_argument("--timeout", type=int, default=120, help="health-wait limit (s)")
    pu.add_argument("--print", "--dry-run", dest="print_only", action="store_true",
                    help="show the compose command without running it")

    pdn = sub.add_parser("down", parents=[_g()], help="stop the stack")
    pdn.add_argument("-v", "--volumes", action="store_true", help="also remove volumes")
    pdn.add_argument("--print", "--dry-run", dest="print_only", action="store_true",
                     help="show the compose command without running it")

    sub.add_parser("prune", parents=[_g()], help="remove built catraz-base images")

    p_run = sub.add_parser(
        "run",
        help="run claude one-off inside the sandbox (drop-in: alias claude='catraz run')")
    p_run.add_argument("claude_args", nargs=argparse.REMAINDER)

    sub.add_parser("status", parents=[_g()], help="health per service, URLs, quota snapshot")

    pl = sub.add_parser("logs", parents=[_g()], help="tail logs (agent|warden|proxy, or --audit)")
    pl.add_argument("service", nargs="?", help="agent | warden | proxy")
    pl.add_argument("-f", "--follow", action="store_true", help="follow")
    pl.add_argument("--tail", type=int, default=100, help="last N lines (default 100)")
    pl.add_argument("--audit", action="store_true", help="warden decision log instead of stdout")

    ps = sub.add_parser("sync", parents=[_g()], help="re-import Claude sandbox credentials from the host")
    ps.add_argument("--from", dest="source", help="source ~/.claude path")
    ps.add_argument("--force", action="store_true", help="overwrite existing credential")

    pa = sub.add_parser("audit", parents=[_g()], help="warden decision log (JSONL tail or --web viewer)")
    pa.add_argument("--web", action="store_true", help="open the live viewer over the admin socket")
    pa.add_argument("-f", "--follow", action="store_true", help="follow")
    pa.add_argument("--tail", type=int, default=100, help="last N lines (default 100)")

    sub.add_parser("version", parents=[_g()], help="show CLI version")
    return p


HANDLERS = {
    "init":    setup.cmd_init,
    "doctor":  setup.cmd_doctor,
    "up":      stack.cmd_up,
    "down":    stack.cmd_down,
    "status":  stack.cmd_status,
    "run":     run_cmd.cmd_run,
    "logs":    observe.cmd_logs,
    "audit":   observe.cmd_audit,
    "sync":    setup.cmd_sync,
    "prune":   cmd_prune,
    "version": cmd_version,
}


def main(argv=None):
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
