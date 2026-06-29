# Remove `up`; rename `down` → `stop` (P3)

> Status: draft · Topic: cli-worklist · Iterations planned: 1

## Goal
Reduce the lifecycle CLI surface. `catraz run` already lazy-starts infra
(`run.py:_ensure_infra` → `compose up -d` when warden+squid aren't healthy), so the
infra-only `catraz up` is redundant. **Remove the `up` command entirely** and rename the
single teardown verb `down` → `stop` (with `down` kept as a back-compat alias). The one
thing `up --remote` did that nothing else does — start the Remote-Control **daemon** — is
re-homed to `catraz run claude-remote` in item 07 (P2), which lands in this same batch, so
the daemon-start gap is closed within the batch.

## Context / constraints
- Pre-release, first write — **no back-compat constraint** (no existing users/scripts).
  A do-nothing `up` returning `EXIT_OK` was considered and rejected (a command that
  reports success while doing nothing is a footgun); we remove `up` outright instead.
- `src/catraz/cli.py`: `up` subparser (99–107), `down` subparser (109–112), `HANDLERS`
  maps `"up": stack.cmd_up`, `"down": stack.cmd_down` (149–150). `cmd_up` is re-exported
  (`from catraz.commands.stack import cmd_up`, cli.py:35) and referenced by tests as
  `cli.cmd_up`.
- `src/catraz/commands/stack.py`: `cmd_up` (47–96) does infra-only `up -d` (or
  `--profile remote up -d` with `--remote`); `cmd_down` (99–114) always tears the remote
  profile too; `_print_urls` (19–27) advertises `catraz up --remote`. Helpers
  `_wait_healthy`, `_print_urls`, `_security_preflight`, `_row_ready` are also used by
  `cmd_status`/`run.py` and **will be reused by item 07's `claude-remote` daemon-start** —
  **keep them**; only `cmd_up` itself is deleted.
- Other user-facing `catraz up` strings: `stack.py:124` (status hint), `doctor.py:298`,
  `observe.py:69`, `setup/__init__.py:90` (post-init "Next:").
- Tests: `tests/cli/test_up_profile.py` exercises `cli.cmd_up` directly → **deleted with
  `cmd_up`**. `tests/cli/test_cli_flag_scoping.py` has `["up", …]` and `["down", …]` cases.
  `tests/redteam/test_shadow_mount.py:86,88` uses `up --remote` and `down`.
- README Basic-commands table + quickstart list `catraz up`, `catraz up --remote`,
  `catraz down`.

## Approach
- **Delete the `up` command surface:** remove the `up` subparser, the `"up"` HANDLERS
  entry, and the `cmd_up` re-export from cli.py; delete `cmd_up` from `stack.py`. Keep all
  the shared helpers (`_wait_healthy`, `_print_urls`, `_security_preflight`, `_row_ready`)
  — item 07 reuses them for `run claude-remote`. (`_wait_healthy` is transiently unused
  between commit 05 and commit 07; acceptable, and a `# noqa`-free plain function is fine.)
- **Rename `down` → `stop`** via argparse alias: `sub.add_parser("stop", aliases=["down"], …)`.
  `args.command` is the literal token invoked (`"stop"` or `"down"` — verified), so
  `HANDLERS` carries **both** keys → `stack.cmd_down` (internal function name unchanged).
- **Re-home the daemon** is item 07's job; this plan only removes `up`. Between these two
  commits there is no daemon-start command — accepted (the batch ends with it restored).
- **Fix the misdirected hints** to point at `catraz run` (they were already
  semi-wrong): the doctor/observe socket hints want infra, which `run` lazy-starts.

## Steps
1. **cli.py** —
   - Delete the `up` subparser (99–107) and the `"up": stack.cmd_up` HANDLERS entry.
   - Delete the `from catraz.commands.stack import cmd_up  # noqa: F401` re-export (cli.py:35).
   - Rename the `down` subparser → `stop` with `aliases=["down"]`, keeping `-v/--volumes`
     and `--print`. In `HANDLERS` add `"stop": stack.cmd_down` and keep `"down": stack.cmd_down`.
2. **stack.py** —
   - Delete `cmd_up` (47–96). Keep `_wait_healthy`, `_print_urls`, `_security_preflight`,
     `_row_ready`, `cmd_down`, `cmd_status`.
   - `_print_urls` (19–27): drop the `catraz up --remote` advertising line. Leave the
     "Remote Control" + "Audit viewer" lines; change the last line to point interactive use
     at `catraz run` only. (Item 07 re-adds a daemon line pointing at `catraz run
     claude-remote`.)
   - `stack.py:124` status hint: "Start it with `catraz up`." → "Start it with `catraz run`."
3. **Other hints** → `catraz run`:
   - `setup/__init__.py:90` post-init "Next: `catraz up`" → "Next: `catraz run`".
   - `doctor.py:298` ("admin socket absent — start with `catraz up`") → `catraz run`.
   - `observe.py:69` ("audit socket not found — run `catraz up` first") → `catraz run`.
4. **Tests** —
   - Delete `tests/cli/test_up_profile.py` (tests the removed `cmd_up`).
   - Add `tests/cli/test_stop_alias.py`: `build_parser().parse_args(["stop"]).command == "stop"`;
     `parse_args(["down"]).command == "down"`; both `"stop"` and `"down"` are in
     `cli.HANDLERS` → `stack.cmd_down`; `parse_args(["stop","-v"]).volumes is True`; and
     `parse_args(["up"])` now raises `SystemExit` (command removed).
   - `tests/cli/test_cli_flag_scoping.py`: remove/adjust any `["up", …]` cases (the command
     is gone — `parse_args(["up", …])` will now error); `["down", …]` cases keep passing via
     the alias. The executor must read this file and fix only the `up` cases.
   - `tests/redteam/test_shadow_mount.py:86,88`: replace `up --remote` with the item-07
     daemon command **only if that test actually runs `up`** — if it is `@slow`/Docker-gated
     and not in the default suite, update the string to `run claude-remote` for consistency;
     otherwise leave a TODO referencing item 07. Read it first.
5. **README** — Basic-commands table + quickstart: remove the `catraz up` and
   `catraz up --remote` rows; rename `catraz down` → `catraz stop` (note `down` alias).
   Point the quickstart at `catraz run` (item 07 will add `catraz run claude-remote` for
   the daemon). Leave a one-line note that the Remote-Control daemon command arrives with
   `run claude-remote`.

## Success criteria
- `catraz up` now errors (`invalid choice`); `grep -rn "cmd_up\|\"up\"" src` is empty
  (except inside multi-word strings / item-07 references).
- `catraz stop` and `catraz down` both tear the stack down; `catraz stop -v` /
  `catraz stop --print` work.
- The four hints point at `catraz run`.
- `pytest tests/cli` green: `test_up_profile.py` removed, `test_stop_alias.py` added,
  flag-scoping `up` cases removed.

## Risks & open questions
- **Daemon-start gap** between commit 05 and commit 07 — accepted per the explicit
  decision (Remote Control isn't startable until `run claude-remote` lands in item 07,
  same batch). The two plans are coupled: 05 keeps the stack-start helpers that 07 consumes.
- If item 07's ordering ever slips out of this batch, the gap becomes user-visible — the
  README note + the batch coupling mitigate this. (Item 07 is planned next.)
- Execution order: edits `cli.py` `build_parser`/`HANDLERS` like items 01/02/04 and
  `stack.py`; run **after** 01–04 and **before** 07. Resolve the parser/HANDLERS block as
  a unit.

## Revision history
- v0: initial draft
- v1 (roast iter 1): accepted both CRITICALs — a no-op `up` returning `EXIT_OK` is a
  footgun, and neutering `up` is churn. Initially rescoped to "rename only + defer `up`".
- v2 (user decision): back-compat does **not** apply (pre-release, first write), and item
  07 (`run claude-remote`) is now in the same batch, so **remove `up` entirely** now and
  let item 07 restore daemon-start. Keep the shared stack helpers for 07 to reuse; delete
  `cmd_up` + `test_up_profile.py`; fix flag-scoping `up` cases.
