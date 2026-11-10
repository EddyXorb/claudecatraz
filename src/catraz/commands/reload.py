"""`catraz reload` — recreate infra services the live apply cannot update.

Policy edits (warden.toml, token files, squid.conf, allowlist.txt) apply live
through each container's supervisor. A recreate is needed for `.env`, for a
changed endpoint host list (network aliases), and for a container that cannot
see the files on disk. `--force` rebuilds every infra service regardless."""

from __future__ import annotations

import argparse
import datetime
from pathlib import Path

from catraz import compose, live_config
from catraz.compose import _rc, compose_ps
from catraz.errors import EXIT_OK
from catraz.ui import Out

# service → config paths it consumes, relative to `.catraz`; the mtime fallback
# for images without a supervisor status file. `.env` applies to both.
SERVICE_CONFIG = {
    "gitlab-warden": list(live_config.INPUTS["gitlab-warden"]),
    "forward-proxy": list(live_config.INPUTS["forward-proxy"]),
}

ENDPOINT_HOSTS_MARKER = "<endpoint hosts>"
OUT_OF_SYNC_MARKER = "<running config out of sync>"


def _newer_than(paths: list[Path], start: datetime.datetime) -> list[Path]:
    changed = []
    for p in paths:
        if not p.exists():
            continue
        mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime, tz=datetime.timezone.utc)
        if mtime > start:
            changed.append(p)
    return changed


def stale_services(
    root: Path, started_at: dict[str, datetime.datetime | None]
) -> dict[str, list[Path | str]]:
    """Map of stale service → reasons (changed Paths or `<…>` markers).

    `started_at` is `{service: datetime|None}`; None (start unreadable) counts
    as stale. A service whose supervisor reports the on-disk config (applied
    or rejected) is never stale for that config. Up-to-date services are omitted."""
    stale: dict[str, list[Path | str]] = {}
    for service, start in started_at.items():
        if start is None:
            stale[service] = ["<unknown start>"]
            continue
        reasons: list[Path | str] = list(_newer_than([root / ".catraz" / ".env"], start))
        state = live_config.live_state(root, service).state
        if state == live_config.OUT_OF_SYNC:
            reasons.append(OUT_OF_SYNC_MARKER)
        elif state == live_config.UNKNOWN:
            paths = [root / ".catraz" / rel for rel in SERVICE_CONFIG.get(service, [])]
            reasons.extend(_newer_than(paths, start))
        if service == "gitlab-warden" and compose.endpoint_routing_changed(root):
            reasons.append(ENDPOINT_HOSTS_MARKER)
        if reasons:
            stale[service] = reasons
    return stale


def cmd_reload(root: Path, args: argparse.Namespace, out: Out) -> int:
    force = getattr(args, "force", False)
    if not (root / ".catraz/.env").exists():
        out.info("Not set up yet — run catraz init.")
        return EXIT_OK

    prefix = compose.prepare(root, render=False)
    rows = compose_ps(root, prefix=prefix)
    if not rows and not force:
        out.info(
            "Stack is not running — nothing to reload (use --force to rebuild + start anyway)."
        )
        return EXIT_OK

    services_running = {r.get("Service") for r in rows}
    endpoint_change = False

    if force:
        # Bypass staleness detection: rebuild + (re)start every infra service, even
        # when the stack is down (mtime checks need a running container to compare).
        targets = sorted(SERVICE_CONFIG)
        out.info(f"• --force: rebuilding {', '.join(targets)}")
    else:
        running_infra = [s for s in sorted(SERVICE_CONFIG) if s in services_running]
        live_config.report(root, out, running_infra)
        started_at = {
            r["Service"]: compose.container_started_at(root, r["Name"], prefix=prefix)
            for r in rows
            if r.get("Service") in SERVICE_CONFIG
        }
        stale = stale_services(root, started_at)
        if not stale:
            out.info(out.green("• config is up to date — edits apply live, nothing to recreate"))
            return EXIT_OK
        for service in sorted(stale):
            items = stale[service]
            endpoint_change = endpoint_change or ENDPOINT_HOSTS_MARKER in items
            names = ", ".join(p.name if isinstance(p, Path) else p for p in items)
            out.warn(f"{service}: needs a recreate ({names})")
        targets = sorted(stale)

    if "claude-dev-env" in services_running:
        out.info(
            "  note: recreating infra briefly interrupts the agent's egress/git "
            "until health returns (the agent itself is not recreated)"
        )
        if endpoint_change:
            out.warn(
                "  the agent routes git hosts by its start-time no_proxy — restart the "
                "agent so it reaches the changed endpoint hosts"
            )

    # --build so a service whose source changed after its image was built is
    # rebuilt, not silently recreated from the stale cached image.
    up_args = ["up", "-d", "--force-recreate", "--build", *targets]
    if args.print_only:
        # render-free prefix from above — do not re-render on a dry-run (like cmd_down's print path)
        compose.run(root, up_args, prefix=prefix, print_only=True)
        return EXIT_OK

    prefix = compose.prepare(root, render=True)
    r = compose.run(root, up_args, prefix=prefix, check=False)
    if r and r.returncode == 0:
        out.info(out.green("• reloaded: " + ", ".join(targets)))
    return _rc(r)
