"""`catraz reload` — recreate infra services the live apply cannot update.

Policy edits (warden.toml, token files, squid.conf, allowlist.txt) apply live
through each container's supervisor. A recreate is needed when the rendered
compose config differs from the one a container was created from, and for a
container that cannot see the files on disk. `--force` rebuilds regardless."""

from __future__ import annotations

import argparse
from pathlib import Path

from catraz import compose, live_config, reconcile
from catraz.compose import _rc, compose_ps
from catraz.errors import EXIT_GENERAL, EXIT_OK
from catraz.ui import Out

COMPOSE_CONFIG_MARKER = "<compose config changed>"
OUT_OF_SYNC_MARKER = "<running config out of sync>"


def stale_services(
    root: Path, running: list[str], drifted: set[str] | None
) -> dict[str, list[str]]:
    """Map of stale running infra service → reasons; an unknown drift (None)
    counts as drifted. A rejected config is not stale: a recreate would hit the
    same invalid file."""
    stale: dict[str, list[str]] = {}
    for service in running:
        reasons = [COMPOSE_CONFIG_MARKER] if drifted is None or service in drifted else []
        if live_config.live_state(root, service).state == live_config.OUT_OF_SYNC:
            reasons.append(OUT_OF_SYNC_MARKER)
        if reasons:
            stale[service] = reasons
    return stale


def cmd_reload(root: Path, args: argparse.Namespace, out: Out) -> int:
    """Holds the reconcile lock, so a background run and a manual one never race.
    With --auto (the background worker) a busy lock means nothing to do, and the
    outcome is left as a notice for the next command."""
    auto = getattr(args, "auto", False)
    with reconcile.infra_lock(root, wait_s=0 if auto else 300) as held:
        if not held:
            if not auto:
                out.err("a background reconcile still holds the infra lock — retry shortly")
            return EXIT_OK if auto else EXIT_GENERAL
        rc, notes = _reload(root, args, out)
    if auto and notes:
        reconcile.notice_path(root).write_text("".join(f"{n}\n" for n in notes), encoding="utf-8")
    return rc


def _reload(root: Path, args: argparse.Namespace, out: Out) -> tuple[int, list[str]]:
    """Exit code plus one-line notes on what changed or needs the user."""
    force = getattr(args, "force", False)
    notes: list[str] = []
    if not (root / ".catraz/.env").exists():
        out.info("Not set up yet — run catraz init.")
        return EXIT_OK, notes

    prefix = compose.prepare(root, render=True)
    rows = compose_ps(root, prefix=prefix)
    if not rows and not force:
        out.info(
            "Stack is not running — nothing to reload (use --force to rebuild + start anyway)."
        )
        return EXIT_OK, notes

    agent = next((r["Name"] for r in rows if r.get("Service") == "claude-dev-env"), None)
    if agent and compose.agent_routing_stale(root, agent):
        notes.append(
            "the agent routes git hosts by its start-time no_proxy — restart the "
            "agent so it reaches the changed endpoint hosts"
        )
        out.warn(notes[-1])

    if force:
        # Also rebuilds a stack that is down, and catches stale bind mounts,
        # which leave the compose config unchanged.
        targets = sorted(compose.INFRA_SERVICES)
        out.info(f"• --force: rebuilding {', '.join(targets)}")
    else:
        running = sorted(r["Service"] for r in rows if r.get("Service") in compose.INFRA_SERVICES)
        live_config.report(root, out, running)
        stale = stale_services(root, running, compose.drifted_services(root, rows, prefix=prefix))
        if not stale:
            out.info(out.green("• config is up to date — edits apply live, nothing to recreate"))
            return EXIT_OK, notes
        for service, reasons in stale.items():
            out.warn(f"{service}: needs a recreate ({', '.join(reasons)})")
        targets = list(stale)

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
        return EXIT_OK, notes

    r = compose.run(root, up_args, prefix=prefix, check=False)
    if r and r.returncode == 0:
        notes.append("recreated " + ", ".join(targets))
        out.info(out.green("• reloaded: " + ", ".join(targets)))
    else:
        notes.append(f"recreating {', '.join(targets)} failed — see {reconcile.log_path(root)}")
    return _rc(r), notes
