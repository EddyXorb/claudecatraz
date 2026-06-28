# `catraz ps` — list active agent containers (P4)

> Status: draft · Topic: cli-worklist · Iterations planned: 1

## Goal
Add `catraz ps`: list the running **agent** containers for this repo's Compose project.
Today only `status` lists infra (warden+squid); there is no view of agent containers
(the `up -d` daemon and any in-flight `run --rm` one-offs). This is the listing half of
P4 — detach/reattach is explicitly **out of scope** (it needs the P2 persistent-mode
lifecycle).

## Context / constraints
- Agent compose service is `claude-dev-env` (`compose.SERVICES["agent"]`).
- **`docker compose run --rm` one-off containers are HIDDEN from `compose ps` unless
  `-a` is passed** — even while running (they carry the `com.docker.compose.oneoff=True`
  label; verified empirically against compose v2). The existing
  `compose_ps` calls `ps --format json` with **no `-a`**, so reusing it as-is would list
  the `up -d` daemon but silently miss every in-flight `catraz run` — defeating the
  feature's headline goal. `cmd_ps` must query with `-a`.
- One-off containers carry their generated names (e.g.
  `<project>-claude-dev-env-run-<hash>`) and `Service == "claude-dev-env"`; the `up -d`
  daemon appears as the plain service container. The `remote`-profile service still shows
  in a non-`--profile` `ps` (verified), so no profile flag is needed.
- `compose.compose_ps(root, prefix=)` already returns parsed rows with `Service`, `State`,
  `Health`, plus `Name` and `Status` (human uptime) fields from `ps --format json`
  (both keys confirmed present in v2 JSON).
- This is observability → put the handler in `src/catraz/commands/observe.py` alongside
  `cmd_logs`/`cmd_audit`. Wire in `cli.py` (`build_parser`, `HANDLERS`).
- Name: **`ps`** (lists the project's agent containers; deliberately scoped, not a raw
  `docker ps`). Exit code is always `EXIT_OK` — it is a query, not a health gate.

## Approach
Query `compose ps -a` (so one-offs are visible), filter rows to
`Service == "claude-dev-env"`, and print a small table (Name, State, uptime). One narrow
piece of new plumbing: an `all` flag on `compose_ps` so the existing default callers stay
unchanged.

## Steps
1. **compose.py** — add `all: bool = False` to `compose_ps`:
   `args = ["ps"] + (["-a"] if all else []) + ["--format", "json"]`. **Do not** flip the
   default — `cmd_status`, `_wait_healthy`, `_ensure_infra` rely on seeing only live
   service containers and must not start surfacing dead one-offs.
2. **observe.py** — add `def cmd_ps(root, args, out)`:
   - `prefix = compose.prepare(root, render=False)`;
     `rows = compose.compose_ps(root, prefix=prefix, all=True)`.
   - `agents = [r for r in rows if r.get("Service") == compose.SERVICES["agent"]]`.
   - If empty: `out.info("No active agent containers.")`; `return EXIT_OK`.
   - Else: `out.head("Agent containers")`, then for each agent row print
     `f"  {name}  {badge}{extra}"` where `name = r.get("Name","?")`,
     `state = r.get("State","?")`, `badge = out.green(state) if state == "running" else
     out.yellow(state)`, `extra = f"  {r.get('Status','')}"` (uptime). Color by `state`
     directly — **no health gate** (`_row_ready` would wrongly down-rank the one-off agent,
     which has no healthcheck). Don't use a fixed `:<N` width: real one-off names exceed
     it and just wrap; ragged is fine. `return EXIT_OK`.
   - `observe.py` already imports `compose` and `EXIT_OK` — no new imports.
3. **cli.py** — `sub.add_parser("ps", parents=[_g()], help="list active agent containers for this repo")`;
   add `"ps": observe.cmd_ps` to `HANDLERS`.
4. **README** — add `| catraz ps | List active agent containers for this repo |` to the
   Basic commands table (near `status`).

## Success criteria
- `pytest tests/cli/test_ps.py` green, covering:
  - **Asserts `all=True` reaches `compose_ps`** (monkeypatch `compose.compose_ps` to
    record its kwargs) — this is the regression guard for the one-off-visibility bug;
    without it the tests pass while the real command lists nothing.
  - Mixed rows (`gitlab-warden`, `forward-proxy`, `claude-dev-env`) → only the
    `claude-dev-env` row is listed; `EXIT_OK`. (monkeypatch `compose.prepare` too.)
  - Two agent rows (a daemon + a `…-run-…` one-off) → both listed.
  - No agent rows → "No active agent containers." + `EXIT_OK`.
  - `compose.compose_ps(all=True)` adds `-a` to the `ps` args (record args via a fake
    `compose.run`); `compose_ps()` default does **not**.
- `catraz ps` with a running `catraz run` shows that one-off container.

## Risks & open questions
- `-a` also surfaces *stopped* one-offs. Harmless here: `catraz run --rm` self-removes, so
  lingering ones are rare; if one lingers, the `state`-based coloring renders it yellow.
- `Name`/`Status` keys are present in v2 JSON (confirmed). Still use `.get(...)` with
  `"?"`/`""` fallbacks so a missing key degrades gracefully rather than `KeyError`.
- Naming collision with `docker ps` is intentional and documented in `--help` ("agent
  containers for this repo"); revisit if it confuses.

## Revision history
- v0: initial draft
- v1 (roast iter 1): accepted the CRITICAL — `docker compose run --rm` one-offs are hidden
  from `ps` without `-a` (verified empirically), so "reuse `compose_ps` as-is" made the
  headline feature a silent no-op, and the mocked tests were blind to it. Added an `all`
  param to `compose_ps` (default unchanged), call it with `all=True`, and added a test
  asserting `-a`/`all=True` is passed. Dropped the fixed column width; corrected the
  (already-satisfied) imports note; retargeted the Risks section onto the `-a` issue.
