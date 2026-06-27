"""Stack lifecycle commands: up, down, status."""
import time

from catraz.errors import CliError, EXIT_OK, EXIT_GENERAL, EXIT_DOCTOR
from catraz.compose import run as compose_run, compose_ps, assert_real_dirs, assert_invariants, _rc
from catraz.doctor import run_doctor, print_findings, SECURITY_SECTIONS
from catraz import auth, image
from catraz.commands.setup import _auto_sync_if_needed


def _row_ready(row):
    state = (row.get("State") or "").lower()
    health = (row.get("Health") or "").lower()
    if state != "running":
        return False
    return health in ("", "healthy")


def _print_urls(out):
    out.head("URLs")
    print("  Remote Control:  " + out.cyan("https://claude.ai")
          + out.dim("  (the agent 'claude-dev-env' registers there)"))
    print("  Audit viewer:    " + out.cyan("catraz audit --web")
          + out.dim("   (host-only, ephemeral loopback port)"))
    # Plain `up` runs infra only (warden+squid). The agent is opt-in:
    print("  Agent daemon:    " + out.cyan("catraz up --remote")
          + out.dim("   ·   interactive: ") + out.cyan("catraz run"))


def _security_preflight(root, out):
    """Run security-section doctor checks; return True if any bad findings."""
    return print_findings(run_doctor(root, only=SECURITY_SECTIONS), out)[0]


def _wait_healthy(root, out, timeout=120):
    out.info(f"• waiting for health (≤{timeout}s)…")
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = compose_ps(root)
        if rows and all(_row_ready(r) for r in rows):
            out.info(out.green("• all services healthy"))
            return
        time.sleep(2)
    out.warn("timed out waiting for health — check `catraz status`")


def cmd_up(root, args, out):
    # Build up_args first — needed by both print_only and real up paths.
    # --profile is a top-level compose flag and must precede the `up` subcommand.
    up_args = (["--profile", "remote"] if args.remote else []) + ["up", "-d"]
    if args.build:
        up_args.append("--build")
    if args.pull:
        up_args.append("--pull=always")

    # Fragment is part of EVERY real up (base_cmd attaches -f when it exists) →
    # a faithful --print must reflect it. Writing is also harmless in dry-run.
    (root / ".catraz").mkdir(exist_ok=True)
    auth.write_auth_fragment(root)

    if args.print_only:
        compose_run(root, up_args, print_only=True)
        return EXIT_OK

    # ── from here Docker/validating: auto-sync, preflight, assert_*, build, up ──
    _auto_sync_if_needed(root, out)
    out.head("— preflight (security checks always run) —")
    if _security_preflight(root, out):
        out.err("preflight failed — fix the ✘ above (or `catraz doctor --fix`)")
        return EXIT_DOCTOR
    print()

    try:
        assert_real_dirs(root)
        assert_invariants(root)
    except CliError as e:
        out.err(str(e))
        return EXIT_DOCTOR

    extra_env = {"BASE_IMAGE": image.resolve_base(root)} if args.remote else None

    out.info("• starting the stack…")
    r = compose_run(root, up_args, check=False, extra_env=extra_env)
    if r.returncode != 0:
        return EXIT_GENERAL

    if not args.no_wait:
        _wait_healthy(root, out, timeout=args.timeout)
    _print_urls(out)
    return EXIT_OK


def cmd_down(root, args, out):
    down_args = ["down"]
    if args.volumes:
        down_args.append("--volumes")
    if args.print_only:
        compose_run(root, down_args, print_only=True)
        return EXIT_OK
    out.info("• stopping the stack…")
    r = compose_run(root, down_args, check=False)
    return _rc(r)


def cmd_status(root, args, out):
    if not (root / ".catraz" / ".env").exists():
        out.info("Not set up yet. Run " + out.bold("catraz init") + ".")
        return EXIT_OK
    rows = compose_ps(root)
    if not rows:
        out.info("Stack is not running. Start it with " + out.bold("catraz up") + ".")
        return EXIT_GENERAL
    out.head("Services")
    all_ready = True
    for r in sorted(rows, key=lambda x: x.get("Service", "")):
        svc = r.get("Service", "?")
        state = r.get("State", "?")
        health = r.get("Health", "")
        ready = _row_ready(r)
        if not ready:
            all_ready = False
        badge = out.green(state) if ready else out.yellow(state)
        extra = f" ({health})" if health else ""
        print(f"  {svc:<16} {badge}{extra}")
    print()
    _print_urls(out)
    return EXIT_OK if all_ready else EXIT_GENERAL
