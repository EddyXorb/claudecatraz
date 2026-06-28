"""`catraz reload` — recreate infra services whose .catraz config changed.

The Warden (and Squid) load their config once at startup, so a config edit is
inert until the container is recreated. This command detects which running infra
services consume a config file (or `.env`) that is newer than the container's
start time, and `up -d --force-recreate`s them. Force-recreate (not `restart`) so
`.env` changes also take effect — env is baked at container create.
"""
import datetime

from catraz.errors import EXIT_OK
from catraz.compose import compose_ps, _rc
from catraz import compose

# service → config paths it consumes, relative to `.catraz`. `.env` is added for
# both in _config_paths (compose interpolation feeds env into both services).
SERVICE_CONFIG = {
    "gitlab-warden": ["config/warden.toml"],
    "forward-proxy": ["config/squid.conf", "config/allowlist.txt"],
}


def _config_paths(root, service):
    """Existing config Paths a service consumes (its files + `.catraz/.env`)."""
    rel = SERVICE_CONFIG.get(service, []) + [".env"]
    paths = [root / ".catraz" / r for r in rel]
    return [p for p in paths if p.exists()]


def stale_services(root, started_at):
    """Map of stale service → list of changed Paths (or `<unknown start>` marker).

    `started_at` is `{service: datetime|None}`, injected so this is unit-testable.
    A service present with a None start time is treated as stale (running but
    unreadable → reload to be safe). Services with no changes are omitted."""
    stale = {}
    for service, start in started_at.items():
        if start is None:
            stale[service] = ["<unknown start>"]
            continue
        changed = []
        for p in _config_paths(root, service):
            mtime = datetime.datetime.fromtimestamp(
                p.stat().st_mtime, tz=datetime.timezone.utc)
            if mtime > start:
                changed.append(p)
        if changed:
            stale[service] = changed
    return stale


def cmd_reload(root, args, out):
    if not (root / ".catraz/.env").exists():
        out.info("Not set up yet — run catraz init.")
        return EXIT_OK

    prefix = compose.prepare(root, render=False)
    rows = compose_ps(root, prefix=prefix)
    if not rows:
        out.info("Stack is not running — nothing to reload.")
        return EXIT_OK

    services_running = {r.get("Service") for r in rows}
    started_at = {
        r["Service"]: compose.container_started_at(root, r["Name"], prefix=prefix)
        for r in rows
        if r.get("Service") in SERVICE_CONFIG
    }

    stale = stale_services(root, started_at)
    if not stale:
        out.info(out.green("• config is up to date — nothing to reload"))
        return EXIT_OK

    for service in sorted(stale):
        items = stale[service]
        names = ", ".join(p.name if hasattr(p, "name") else str(p) for p in items)
        out.warn(f"{service}: stale config ({names})")
    if "claude-dev-env" in services_running:
        out.info("  note: recreating infra briefly interrupts the agent's egress/git "
                 "until health returns (the agent itself is not recreated)")

    up_args = ["up", "-d", "--force-recreate", *sorted(stale)]
    if args.print_only:
        # render-free prefix from above — do not re-render on a dry-run (matches cmd_up)
        compose.run(root, up_args, prefix=prefix, print_only=True)
        return EXIT_OK

    prefix = compose.prepare(root, render=True)
    r = compose.run(root, up_args, prefix=prefix, check=False)
    if r and r.returncode == 0:
        out.info(out.green("• reloaded: " + ", ".join(sorted(stale))))
    return _rc(r)
