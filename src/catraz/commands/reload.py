"""`catraz reload` — recreate infra services the live apply cannot update.

Policy edits (warden.toml, token files, squid.conf, allowlist.txt) apply live
through each container's supervisor. A recreate is needed when the rendered
compose config differs from the one a container was created from, and for a
container that cannot see the files on disk. `--force` rebuilds regardless."""

from __future__ import annotations

import argparse
import datetime
from pathlib import Path

from catraz import compose, live_config
from catraz.compose import _rc, compose_ps
from catraz.errors import EXIT_OK
from catraz.ui import Out

COMPOSE_CONFIG_MARKER = "<compose config changed>"
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
    root: Path, started_at: dict[str, datetime.datetime | None], drifted: set[str] | None
) -> dict[str, list[Path | str]]:
    """Map of stale service → reasons (changed Paths or `<…>` markers).

    A None start time or an unknown drift (None) counts as stale. A service whose
    supervisor reports the on-disk config (applied or rejected) is never stale
    for that config. Up-to-date services are omitted."""
    stale: dict[str, list[Path | str]] = {}
    for service, start in started_at.items():
        if start is None:
            stale[service] = ["<unknown start>"]
            continue
        reasons: list[Path | str] = []
        if drifted is None or service in drifted:
            reasons.append(COMPOSE_CONFIG_MARKER)
        state = live_config.live_state(root, service).state
        if state == live_config.OUT_OF_SYNC:
            reasons.append(OUT_OF_SYNC_MARKER)
        elif state == live_config.UNKNOWN:
            reasons.extend(_newer_than(live_config.input_paths(root, service), start))
        if reasons:
            stale[service] = reasons
    return stale


def cmd_reload(root: Path, args: argparse.Namespace, out: Out) -> int:
    force = getattr(args, "force", False)
    if not (root / ".catraz/.env").exists():
        out.info("Not set up yet — run catraz init.")
        return EXIT_OK

    prefix = compose.prepare(root, render=True)
    rows = compose_ps(root, prefix=prefix)
    if not rows and not force:
        out.info(
            "Stack is not running — nothing to reload (use --force to rebuild + start anyway)."
        )
        return EXIT_OK

    agent = next((r["Name"] for r in rows if r.get("Service") == "claude-dev-env"), None)
    if agent and compose.agent_routing_stale(root, agent):
        out.warn(
            "the agent routes git hosts by its start-time no_proxy — restart the "
            "agent so it reaches the changed endpoint hosts"
        )

    if force:
        # Also rebuilds a stack that is down, and catches stale bind mounts,
        # which leave the compose config unchanged.
        targets = sorted(compose.INFRA_SERVICES)
        out.info(f"• --force: rebuilding {', '.join(targets)}")
    else:
        infra_rows = [r for r in rows if r.get("Service") in compose.INFRA_SERVICES]
        live_config.report(root, out, sorted(r["Service"] for r in infra_rows))
        started_at = {
            r["Service"]: compose.container_started_at(root, r["Name"], prefix=prefix)
            for r in infra_rows
        }
        stale = stale_services(
            root, started_at, compose.drifted_services(root, rows, prefix=prefix)
        )
        if not stale:
            out.info(out.green("• config is up to date — edits apply live, nothing to recreate"))
            return EXIT_OK
        for service in sorted(stale):
            names = ", ".join(p.name if isinstance(p, Path) else p for p in stale[service])
            out.warn(f"{service}: needs a recreate ({names})")
        targets = sorted(stale)

    if agent:
        out.info(
            "  note: recreating infra briefly interrupts the agent's egress/git "
            "until health returns (the agent itself is not recreated)"
        )

    # --build so a service whose source changed after its image was built is
    # rebuilt, not silently recreated from the stale cached image.
    up_args = ["up", "-d", "--force-recreate", "--build", *targets]
    if args.print_only:
        compose.run(root, up_args, prefix=prefix, print_only=True)
        return EXIT_OK

    r = compose.run(root, up_args, prefix=prefix, check=False)
    if r and r.returncode == 0:
        out.info(out.green("• reloaded: " + ", ".join(targets)))
    return _rc(r)
