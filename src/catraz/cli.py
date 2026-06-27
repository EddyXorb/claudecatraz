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
import contextlib
import getpass
import os
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from catraz import __version__
from catraz.errors import (
    CliError, EXIT_OK, EXIT_GENERAL, EXIT_CONFIG, EXIT_DOCTOR, EXIT_DOCKER,
)
from catraz.envfile import load_env, set_env_values, mask
from catraz.policy import validate_project, _resolve_allowed_projects
from catraz.compose import run as compose_run, compose_ps, resolve_service, SERVICES, assert_real_dirs, assert_invariants
from catraz.doctor import (
    run_doctor, print_findings, _doctor_fix, DOCTOR_SECTIONS, SECURITY_SECTIONS,
    SECRETS,
)
from catraz import auth

COMPONENT_VARS = [
    "UV_VERSION", "CLANG_VERSION", "RUST_VERSION",
    "CONAN_VERSION", "NODE_VERSION", "CLAUDE_CODE_VERSION",
]


# ── styling ───────────────────────────────────────────────────────────────────

class Out:
    """ANSI styling that quietly disables itself for non-TTYs / --no-color."""

    def __init__(self, color=True):
        self.color = color and sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

    def _c(self, code, s):
        return f"\033[{code}m{s}\033[0m" if self.color else s

    def bold(self, s): return self._c("1", s)
    def dim(self, s): return self._c("2", s)
    def green(self, s): return self._c("32", s)
    def yellow(self, s): return self._c("33", s)
    def red(self, s): return self._c("31", s)
    def cyan(self, s): return self._c("36", s)

    def head(self, s): print(self.bold(s))
    def info(self, s): print(s)
    def warn(self, s): print(self.yellow(f"warning: {s}"), file=sys.stderr)
    def err(self, s): print(self.red(f"error: {s}"), file=sys.stderr)


# Exit codes + CliError live in catraz.errors (imported above) to avoid an
# import cycle with paths.py/compose.py/doctor.py.


# ── commands ────────────────────────────────────────────────────────────────────

def cmd_doctor(root, args, out):
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

    # 3. secrets
    if args.yes:
        out.info("• --yes: keeping existing .env values, skipping prompts")
    else:
        print()
        for key, prompt in SECRETS:
            cur = env.get(key, "")
            if cur and not args.force:
                out.info(f"  {key} already set ({mask(cur)}) — keeping. Use --force to change.")
                continue
            val = getpass.getpass(f"  {prompt}\n  {key}: ").strip()
            if val:
                updates[key] = val
            elif not cur:
                out.warn(f"{key} left empty — doctor will flag it")

        # 4. allowed projects (the roast fix: without this the warden won't start)
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
            import json
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


def cmd_migrate(root, args, out):
    cat = root / ".catraz"; cat.mkdir(exist_ok=True)
    moves = {"config": "config", "state": "state", "logs": "logs",
             "claude": "claude", ".env": ".env"}
    for src_name, dst_name in moves.items():
        src = root / src_name; dst = cat / dst_name
        if src.exists() and not dst.exists():
            src.rename(dst)               # atomic move, same filesystem
    # fail-closed: kein Alt-Layout-Secret darf unter root verbleiben
    leftovers = [n for n in ("claude", "state", ".env") if (root / n).exists()]
    if leftovers:
        raise CliError(
            f"migration incomplete, still under project root: {leftovers}", EXIT_CONFIG)
    _ensure_gitignore(root)
    out.info(out.green("migrated to .catraz/"))
    return EXIT_OK


def _run_sync(root, out, source=None, force=False):
    entry = root / "src" / "catraz" / "assets" / "container" / "entrypoint.py"
    if not entry.exists():
        raise CliError("entrypoint.py not found", EXIT_GENERAL)
    env = load_env(root / ".catraz" / ".env")
    from catraz.paths import claude_home
    home = claude_home(root)
    cmd = [sys.executable, str(entry), "sync", "--claude-home", str(home)]
    src = source or env.get("CLAUDE_CREDENTIAL_SOURCE")
    if src:
        cmd += ["--from", str(Path(src).expanduser())]
    r = subprocess.run(cmd, cwd=root, env=dict(os.environ))
    if r.returncode != 0:
        raise CliError("credential sync failed", EXIT_GENERAL)


def cmd_sync(root, args, out):
    try:
        _run_sync(root, out, source=args.source, force=args.force)
    except CliError as e:
        out.err(str(e))
        return e.code
    return EXIT_OK


def cmd_up(root, args, out):
    if not args.print_only:
        # Auto-sync: in subscription mode, materialize the credential before preflight.
        # If it stays missing, the "auth" security section fails closed below.
        from catraz.paths import claude_home
        mode = load_env(root / ".catraz" / ".env").get("AUTH_MODE", "subscription")
        if mode == "subscription" and not (claude_home(root) / ".credentials.json").exists():
            out.info("• subscription credential missing — attempting sync…")
            try:
                _run_sync(root, out)
            except CliError as e:
                out.warn(str(e) + " — run `catraz sync` once authenticated")

        out.head("— preflight (security checks always run) —")
        f = run_doctor(root, only=SECURITY_SECTIONS)
        bad, _ = print_findings(f, out)
        if bad:
            out.err("preflight failed — fix the ✘ above (or `catraz doctor --fix`)")
            return EXIT_DOCTOR
        print()

    # Ensure .catraz/ exists (init creates it; defensive mkdir in case it's missing).
    (root / ".catraz").mkdir(exist_ok=True)

    auth.write_auth_fragment(root)

    try:
        assert_real_dirs(root)
        assert_invariants(root)
    except CliError as e:
        out.err(str(e))
        return EXIT_DOCTOR

    up_args = ["up", "-d"]
    if args.build:
        up_args.append("--build")
    if args.pull:
        up_args.append("--pull=always")
    if args.print_only:
        compose_run(root, up_args, print_only=True)
        return EXIT_OK

    out.info("• starting the stack…")
    r = compose_run(root, up_args, check=False)
    if r.returncode != 0:
        return EXIT_GENERAL

    if not args.no_wait:
        _wait_healthy(root, out, timeout=args.timeout)
    _print_urls(out)
    return EXIT_OK


def _wait_healthy(root, out, timeout=120):
    import time
    out.info(f"• waiting for health (≤{timeout}s)…")
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = compose_ps(root)
        if rows and all(_row_ready(r) for r in rows):
            out.info(out.green("• all services healthy"))
            return
        time.sleep(2)
    out.warn("timed out waiting for health — check `catraz status`")


def _row_ready(row):
    state = (row.get("State") or "").lower()
    health = (row.get("Health") or "").lower()
    if state != "running":
        return False
    return health in ("", "healthy")


def cmd_down(root, args, out):
    down_args = ["down"]
    if args.volumes:
        down_args.append("--volumes")
    if args.print_only:
        compose_run(root, down_args, print_only=True)
        return EXIT_OK
    out.info("• stopping the stack…")
    r = compose_run(root, down_args, check=False)
    return EXIT_OK if r.returncode == 0 else EXIT_GENERAL


def cmd_status(root, args, out):
    if not (root / ".catraz" / ".env").exists():
        out.info("Not set up yet. Run " + out.bold("catraz init") + ".")
        return EXIT_OK
    rows = compose_ps(root)
    if not rows:
        out.info("Stack is not running. Start it with " + out.bold("catraz up") + ".")
        return EXIT_OK
    out.head("Services")
    for r in sorted(rows, key=lambda x: x.get("Service", "")):
        svc = r.get("Service", "?")
        state = r.get("State", "?")
        health = r.get("Health", "")
        badge = out.green(state) if _row_ready(r) else out.yellow(state)
        extra = f" ({health})" if health else ""
        print(f"  {svc:<16} {badge}{extra}")
    print()
    _print_urls(out)
    return EXIT_OK


def _print_urls(out):
    out.head("URLs")
    print("  Remote Control:  " + out.cyan("https://claude.ai")
          + out.dim("  (the agent 'claude-dev-env' registers there)"))
    print("  Audit viewer:    " + out.cyan("catraz audit --web")
          + out.dim("   (host-only, ephemeral loopback port)"))


def cmd_logs(root, args, out):
    log_args = ["logs"]
    if args.audit:
        return _tail_audit(root, args, out)
    if args.follow:
        log_args.append("-f")
    log_args += ["--tail", str(args.tail)]
    if args.service:
        log_args.append(resolve_service(args.service))
    r = compose_run(root, log_args, check=False)
    return EXIT_OK if r and r.returncode == 0 else EXIT_GENERAL


def _tail_audit(root, args, out):
    d = root / ".catraz" / "logs" / "warden"
    files = sorted(d.glob("*.jsonl")) if d.exists() else []
    if not files:
        out.warn(f"no audit logs in {d}")
        return EXIT_OK
    cmd = ["tail"]
    if args.follow:
        cmd.append("-f")
    cmd += ["-n", str(args.tail), *map(str, files)]
    subprocess.run(cmd)
    return EXIT_OK


class _UdsProxy(socketserver.BaseRequestHandler):
    sock_path = ""           # per-instance via type(...)

    def handle(self):
        with socket.socket(socket.AF_UNIX) as up:
            up.connect(self.sock_path)

            def fwd(a, b):
                try:
                    while (d := a.recv(65536)):
                        b.sendall(d)
                except OSError:
                    pass
                finally:
                    with contextlib.suppress(OSError):
                        b.shutdown(socket.SHUT_WR)
            t = threading.Thread(target=fwd, args=(self.request, up), daemon=True)
            t.start()
            fwd(up, self.request)
            t.join()


def cmd_audit(root, args, out):
    sock = root / ".catraz/run/warden/admin.sock"
    if not args.web:
        return _tail_audit(root, args, out)            # bestehender JSONL-Tail
    if not sock.exists():
        out.err("audit socket not found — run `catraz up` first")
        return EXIT_GENERAL
    handler = type("H", (_UdsProxy,), {"sock_path": str(sock)})
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)   # ephemerer Port
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    out.info(f"audit viewer: {url}  (Ctrl-C to stop)")
    webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()
    return EXIT_OK


def cmd_version(root, out):
    print(f"catraz {__version__}")
    env = load_env(root / ".catraz" / ".env") if root else {}
    if env:
        out.head("\nComponent versions (.env)")
        for k in COMPONENT_VARS:
            if env.get(k):
                print(f"  {k:<20} {env[k]}")


# ── argument parsing ────────────────────────────────────────────────────────────

def _g():
    """A parent parser carrying the global flags, for subparsers to inherit."""
    parent = argparse.ArgumentParser(add_help=False)
    add_global(parent)
    return parent


def add_global(parser):
    """Global flags, repeated on every subparser so they work before *or* after the
    subcommand. SUPPRESS defaults keep a value given before the subcommand from being
    clobbered by the subparser's own default (the classic argparse gotcha)."""
    parser.add_argument("-C", "--dir", default=argparse.SUPPRESS,
                        help="project root (default: dir with .catraz/)")
    parser.add_argument("--print", "--dry-run", dest="print_only", action="store_true",
                        default=argparse.SUPPRESS,
                        help="show the compose command without running it (up/down)")
    parser.add_argument("-y", "--yes", action="store_true", default=argparse.SUPPRESS,
                        help="non-interactive; accept defaults")
    parser.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS,
                        help="disable ANSI colors")


def build_parser():
    p = argparse.ArgumentParser(
        prog="catraz",
        description="Front door for the claudecatraz stack. Start with `catraz init`.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_global(p)
    p.add_argument("-V", "--version", action="store_true", help="show versions and exit")
    sub = p.add_subparsers(dest="command")

    pi = sub.add_parser("init", parents=[_g()], help="interactive setup session (the wizard)")
    pi.add_argument("--force", action="store_true", help="re-prompt even for set values")
    pi.add_argument("--skip-sync", action="store_true", help="skip the Claude credential import")

    sub.add_parser("migrate", parents=[_g()],
                   help="move a legacy layout (./config, ./state, ./.env, …) into .catraz/")

    pd = sub.add_parser("doctor", parents=[_g()], help="preflight: turn silent setup failures loud")
    pd.add_argument("--fix", action="store_true", help="repair safe findings (dirs, chown)")
    pd.add_argument("--strict", action="store_true", help="warnings count as failures (exit 3)")
    pd.add_argument("--section", choices=DOCTOR_SECTIONS, help="run only one section")

    pu = sub.add_parser("up", parents=[_g()], help="start the stack, wait for health, print URLs")
    pu.add_argument("--build", action="store_true", help="rebuild images first")
    pu.add_argument("--pull", action="store_true", help="pull base images first")
    pu.add_argument("--no-wait", action="store_true", help="don't wait for health")
    pu.add_argument("--timeout", type=int, default=120, help="health-wait limit (s)")

    pdn = sub.add_parser("down", parents=[_g()], help="stop the stack")
    pdn.add_argument("-v", "--volumes", action="store_true", help="also remove volumes")

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

    sub.add_parser("version", parents=[_g()], help="show CLI + component versions")
    return p


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
        cmd_version(root, out)
        return EXIT_OK

    if not args.command:
        parser.print_help()
        return EXIT_OK

    # init/migrate run BEFORE a .catraz exists → they take the explicit dir (or CWD)
    # as root rather than walking up for an existing .catraz.
    if args.command in ("init", "migrate"):
        root = Path(args.dir).resolve() if args.dir else Path.cwd().resolve()
        try:
            if args.command == "init":
                return cmd_init(root, args, out)
            return cmd_migrate(root, args, out)
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
        if args.command == "doctor":
            return cmd_doctor(root, args, out)
        if args.command == "up":
            return cmd_up(root, args, out)
        if args.command == "down":
            return cmd_down(root, args, out)
        if args.command == "status":
            return cmd_status(root, args, out)
        if args.command == "logs":
            return cmd_logs(root, args, out)
        if args.command == "sync":
            return cmd_sync(root, args, out)
        if args.command == "audit":
            return cmd_audit(root, args, out)
        if args.command == "version":
            cmd_version(root, out)
            return EXIT_OK
    except CliError as e:
        out.err(str(e))
        return e.code
    except KeyboardInterrupt:
        print()
        return EXIT_GENERAL
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
