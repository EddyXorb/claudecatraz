"""Stack lifecycle commands: stop, status."""
import time

from catraz.errors import CliError, EXIT_OK, EXIT_GENERAL, EXIT_DOCTOR
from catraz.compose import run as compose_run, compose_ps, assert_real_dirs, assert_invariants, _rc
from catraz.doctor import run_doctor, print_findings, SECURITY_SECTIONS
from catraz import auth, image, compose
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
    # `run` lazy-starts infra (warden+squid) and runs the agent one-off:
    print("  Interactive:     " + out.cyan("catraz run"))


def _security_preflight(root, out):
    """Run security-section doctor checks; return True if any bad findings."""
    return print_findings(run_doctor(root, only=SECURITY_SECTIONS), out)[0]


def _wait_healthy(root, out, prefix=None, timeout=120):
    out.info(f"• waiting for health (≤{timeout}s)…")
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = compose_ps(root, prefix=prefix)
        if rows and all(_row_ready(r) for r in rows):
            out.info(out.green("• all services healthy"))
            return
        time.sleep(2)
    out.warn("timed out waiting for health — check `catraz status`")


def cmd_down(root, args, out):
    # `--profile remote` brings the agent service (profiles: ["remote"]) into scope so a
    # plain `down` actually tears it down. Without it the agent container survives, pinned
    # to the now-deleted agent-net → "network <id> not found" on the next `up --remote`.
    # (--remove-orphans alone is unreliable here: a profile-disabled service is not always
    # treated as an orphan.) --remove-orphans additionally clears any truly-stale leftovers.
    down_args = ["--profile", "remote", "down", "--remove-orphans"]
    if args.volumes:
        down_args.append("--volumes")
    if args.print_only:
        compose_run(root, down_args, print_only=True)
        return EXIT_OK
    out.info("• stopping the stack…")
    prefix = compose.prepare(root, render=True)
    r = compose_run(root, down_args, prefix=prefix, check=False)
    return _rc(r)


def cmd_status(root, args, out):
    if not (root / ".catraz" / ".env").exists():
        out.info("Not set up yet. Run " + out.bold("catraz init") + ".")
        return EXIT_OK
    prefix = compose.prepare(root, render=False)
    rows = compose_ps(root, prefix=prefix)
    if not rows:
        out.info("Stack is not running. Start it with " + out.bold("catraz run") + ".")
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
