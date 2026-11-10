"""Stack lifecycle commands: stop, status."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from catraz.errors import EXIT_OK, EXIT_GENERAL
from catraz.compose import run as compose_run, compose_ps, _rc
from catraz.doctor import run_doctor, print_findings, SECURITY_SECTIONS
from catraz import compose, live_config
from catraz.ui import Out


def _row_ready(row: dict[str, str]) -> bool:
    state = (row.get("State") or "").lower()
    health = (row.get("Health") or "").lower()
    if state != "running":
        return False
    return health in ("", "healthy")


def _print_urls(out: Out) -> None:
    out.head("URLs")
    print(
        "  Remote Control:  "
        + out.cyan("https://claude.ai")
        + out.dim("  (the agent 'claude-dev-env' registers there)")
    )
    print(
        "  Audit viewer:    "
        + out.cyan("catraz audit --web")
        + out.dim("   (host-only, ephemeral loopback port)")
    )
    # `claude-remote` starts the agent daemon; plain `run` is the interactive one-off.
    print(
        "  Agent daemon:    "
        + out.cyan("catraz run claude-remote")
        + out.dim("   ·   interactive: ")
        + out.cyan("catraz run")
    )


def _security_preflight(root: Path, out: Out) -> bool:
    """Run security-section doctor checks; return True if any bad findings."""
    return bool(print_findings(run_doctor(root, only=SECURITY_SECTIONS), out)[0])


def _wait_healthy(
    root: Path, out: Out, prefix: list[str] | None = None, timeout: int = 120
) -> None:
    out.info(f"• waiting for health (≤{timeout}s)…")
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = compose_ps(root, prefix=prefix)
        if rows and all(_row_ready(r) for r in rows):
            out.info(out.green("• all services healthy"))
            return
        time.sleep(2)
    out.warn("timed out waiting for health — check `catraz status`")


_LIVE_LABELS = {
    live_config.APPLIED: "applied live",
    live_config.PENDING: "change pending (picked up within seconds)",
    live_config.REJECTED: "REJECTED — denying all traffic",
    live_config.OUT_OF_SYNC: "out of sync — run catraz reload",
    live_config.UNKNOWN: "no live status (image predates live apply — catraz reload --force)",
}


def _print_live_config(root: Path, running: set[str], out: Out) -> bool:
    """Config section for running infra; False if any service is not enforcing
    the config on disk."""
    services = [s for s in sorted(live_config.INPUTS) if s in running]
    if not services:
        return True
    out.head("Config")
    good = True
    for svc in services:
        ls = live_config.live_state(root, svc)
        label = _LIVE_LABELS[ls.state]
        if ls.state in (live_config.APPLIED, live_config.PENDING):
            badge = out.green(label)
        elif ls.state == live_config.UNKNOWN:
            badge = out.yellow(label)
        else:
            good = False
            badge = out.red(label)
        print(f"  {svc:<16} {badge}")
        if ls.detail:
            print(f"  {'':<16} {out.red(ls.detail)}")
    return good


def cmd_down(root: Path, args: argparse.Namespace, out: Out) -> int:
    # `--profile remote` brings the agent service into scope so `down` actually tears
    # it down (otherwise it survives, pinned to the now-deleted network).
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


def cmd_status(root: Path, args: argparse.Namespace, out: Out) -> int:
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
    if not _print_live_config(root, {r.get("Service", "") for r in rows}, out):
        all_ready = False
    print()
    _print_urls(out)
    return EXIT_OK if all_ready else EXIT_GENERAL
