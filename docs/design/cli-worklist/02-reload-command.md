# `catraz reload` — restart services with stale config (P8)

> Status: draft · Topic: cli-worklist · Iterations planned: 1

## Goal
Add `catraz reload`: detect when `.catraz` config has changed since the running
containers started, warn, and recreate the affected services so they pick up the new
config. Pure runtime ergonomics — the Warden loads its `Config` once at startup
(`warden/config.py`, no reload path), so a config edit is otherwise inert until manual
restart.

## Context / constraints
- The infra services that read host config files:
  - **gitlab-warden** reads `.catraz/config/warden.toml` (bind, ro) + env from `.env`
    (env vars in `docker-compose.yml:18-36`).
  - **forward-proxy** reads `.catraz/config/squid.conf` + `.catraz/config/allowlist.txt`
    (binds, ro) + `SQUID_PORT` from `.env`.
  - `.catraz/.env` feeds both via compose interpolation.
- Compose call prefix + helpers live in `src/catraz/compose.py`:
  `compose_ps(root, prefix=)` → `[{Service,State,Health,…}]`; `prepare(root, render=)`;
  `run(root, args, prefix=, …)`; `project_name(root)`.
- `restart` alone does **not** re-read env (env is baked at container create), so use
  `up -d --force-recreate <services>` — correct for both file and env changes.
- Naming is settled: **`reload`** (per triage: not `init`/`reinit`; `reinit` sounds
  destructive). It is a lifecycle op, distinct from onboarding (`init`).
- Command wiring mirrors existing subcommands in `src/catraz/cli.py` (`build_parser`,
  `HANDLERS`) and a handler module under `src/catraz/commands/`.

## Approach
Compare each **running** infra service's container `StartedAt` against the mtime of the
config files that service consumes. If any file is newer → service is stale. Warn, then
re-render `compose.resolved.yml` and `up -d --force-recreate` the stale services. Always
force-recreate (vs `restart`) so `.env` edits also take effect (env is baked at create).

**Fail toward reloading, never toward a false "up to date".** `started_at` is built only
for services that are actually in the `compose ps` rows (i.e. running). For such a
service, a `None` start time means "couldn't read/parse `docker inspect`" — that must be
treated as **stale** (reload to be safe), *not* skipped. Absent services simply never
enter the dict. This avoids the trap of `None` meaning both "not running" and "unknown".

## Steps
1. **compose.py** — add `container_started_at(root, name_or_id, *, prefix=None) -> datetime | None`:
   - The `compose ps --format json` rows already carry the container `Name` (and `ID`);
     pass that in — **do not** issue a separate `ps -q` per service.
   - `r = subprocess.run(["docker","inspect","-f","{{.State.StartedAt}}", name_or_id], capture_output=True, text=True)`;
     on non-zero / empty / `FileNotFoundError` → `return None`.
   - Parse via module-level `_parse_docker_time(s) -> datetime | None`: 3.11+
     `datetime.fromisoformat` already accepts arbitrary fractional-second digits and a
     trailing `Z` — so just `s = s.strip().replace("Z", "+00:00")`,
     `try: return datetime.datetime.fromisoformat(s) except ValueError: return None`.
     **No digit-stripping** (`requires-python >=3.11`; verified `fromisoformat` parses
     9-digit nanoseconds and truncates to micros). Keep a unit test as a regression pin
     (9-digit nanos, 6-digit, no fraction, garbage→None).
2. **New module `src/catraz/commands/reload.py`**:
   - `SERVICE_CONFIG = {"gitlab-warden": ["config/warden.toml"], "forward-proxy": ["config/squid.conf", "config/allowlist.txt"]}`
     — service → config paths relative to `.catraz`. (`.env` is added in `_config_paths` for both.)
   - `def _config_paths(root, service)` → list of existing `Path`s: each entry in
     `SERVICE_CONFIG[service]` plus `.catraz/.env`, filtered to `.exists()`.
   - `def stale_services(root, started_at)`: takes injected `started_at`
     `{service: datetime|None}` (timestamps injected → unit-testable; it still does the
     mtime `stat()` itself, so it is *I/O-light*, not strictly pure). For each service
     **present in the dict**:
       - if its start time is `None` → service is stale with reason `["<unknown start>"]`
         (running but unreadable → reload).
       - else a path is changed iff
         `datetime.datetime.fromtimestamp(path.stat().st_mtime, tz=datetime.timezone.utc)`
         `>` the start time. **Pin the `tz=timezone.utc`** — `st_mtime` is epoch-UTC, and a
         naive `fromtimestamp` would mis-compare by the local offset against the UTC
         `StartedAt`.
     Return `{service: [changed Path|marker, …]}`, omitting services with no changes.
   - `def cmd_reload(root, args, out)`:
     - If `not (root/".catraz/.env").exists()` → `out.info("Not set up yet — run catraz init.")`, `return EXIT_OK`.
     - `prefix = compose.prepare(root, render=False)`; `rows = compose_ps(root, prefix=prefix)`.
     - If no rows → `out.info("Stack is not running — nothing to reload.")`, `return EXIT_OK`.
     - Build `started_at` only for infra services (`SERVICE_CONFIG` keys) **present in
       `rows`**, via `compose.container_started_at(root, row["Name"], prefix=prefix)`.
     - `stale = stale_services(root, started_at)`.
     - If empty → `out.info(out.green("• config is up to date — nothing to reload"))`, `return EXIT_OK`.
     - Else: `out.warn(...)` listing each stale service and the changed file basenames
       (or the `<unknown start>` marker). If an agent (`claude-dev-env`) is in `rows`,
       add a one-line `out.info` heads-up that recreating infra briefly interrupts the
       agent's egress/git until health returns (the agent container itself is **not**
       recreated — `depends_on` flows agent→infra, not the reverse).
       - If `args.print_only`: `compose.run(root, ["up","-d","--force-recreate",*sorted(stale)], prefix=prefix, print_only=True)` using the **render-free** `prefix` from above (do **not** re-render on a dry-run, matching `cmd_up`'s print path) and `return EXIT_OK`.
       - Otherwise re-render: `prefix = compose.prepare(root, render=True)`, then
         `r = compose.run(root, ["up","-d","--force-recreate",*sorted(stale)], prefix=prefix, check=False)`;
         on success `out.info(out.green("• reloaded: " + ", ".join(sorted(stale))))`; `return _rc(r)`.
3. **cli.py** — add subparser `pr = sub.add_parser("reload", parents=[_g()], help="restart services whose .catraz config changed")` with `pr.add_argument("--print","--dry-run", dest="print_only", action="store_true", help="show the compose command without running it")`; import the module as `from catraz.commands import reload as reload_cmd` (alias purely for a readable `HANDLERS` line — `reload` is **not** a Py3 builtin, no shadowing concern); add `"reload": reload_cmd.cmd_reload` to `HANDLERS`.
4. **README** — add a `| catraz reload | Restart Warden/Squid after editing .catraz config |`
   row to the Basic commands table (after `status`).

## Success criteria
- `pytest tests/cli/test_reload.py` green, covering:
  - `_parse_docker_time` for 9-digit-nanos / 6-digit / no-fraction / garbage(→None).
  - `stale_services` **tight window** (catches the tz bug): `started_at = now(UTC)`, a
    config file `stat`ed a couple seconds later → service listed; `started_at = now(UTC)`
    a moment in the future, untouched file → not listed.
  - `stale_services` `None` start time for a present service → service listed (treated as
    stale, not skipped).
  - `cmd_reload` "up to date" path returns `EXIT_OK` and makes **no** `up` call
    (monkeypatch `compose_ps` + `container_started_at` so nothing is stale).
  - `cmd_reload` stale path issues `up -d --force-recreate <service>` (record args via a
    fake `compose.run`).
- `catraz reload` on an unchanged, running stack prints "up to date" and recreates nothing.

## Risks & open questions
- `docker inspect` time format: `_parse_docker_time` + its regression test pin the
  contract. On parse failure for a *running* container, start time is `None` → treated as
  **stale** (reload), never as "up to date". (This is the corrected semantics — the old
  draft had it backwards.)
- Timezone: `st_mtime` is epoch-UTC; the conversion is pinned to `tz=utc` so it compares
  correctly against the UTC `StartedAt`. Clock skew on one host is negligible.
- Recreating infra while the agent daemon runs briefly interrupts the agent's
  egress/git until health returns; the command warns. The agent container is not itself
  recreated (it is omitted from the `--force-recreate` service list, and `depends_on`
  does not cascade backward).
- Scope: only infra (warden, proxy) is reloaded; agent containers are out of scope.

## Revision history
- v0: initial draft
- v1 (roast iter 1): accepted the CRITICAL — the old `None`-means-skip + "up to date"
  combo could falsely report a stale-config warden as fine; flipped to "unknown start ⇒
  reload". Accepted: drop dead digit-stripping in `_parse_docker_time` (3.11
  `fromisoformat` handles nanos), pin `tz=utc` mtime conversion + add a tight-window
  test, pass row `Name` to `container_started_at` instead of a redundant `ps -q`,
  render-free `--print` path, and corrected the `reload_cmd` rationale (not a builtin).
  Added an optional agent-running heads-up.
