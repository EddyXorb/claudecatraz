# Named run modes: `run claude` / `claude-remote` / `shell` (P2)

> Status: draft · Topic: cli-worklist · Iterations planned: 1

## Goal
Unify the launch surface under one verb with named modes:
`catraz run [<mode>] [-- <args>]`, where `<mode>` is `claude` (default),
`claude-remote`, or `shell`. This retires the separate `shell` subcommand and re-homes the
daemon-start that item 05 removed (`up --remote`) to `catraz run claude-remote`. Modes are
**hardcoded built-ins**, not a plugin/asset system (deferred until a real 4th mode exists).

## Context / constraints
- **Depends on item 05** (removed `cmd_up`; kept the stack helpers `_security_preflight`,
  `_wait_healthy`, `_print_urls`, `_row_ready`) and **item 03** (added non-TTY transcript
  teeing to `cmd_run`'s `claude` path via `compose.run(..., tee=…)`). This plan restructures
  `cmd_run` into a mode dispatcher and **must preserve item 03's teeing** in the `claude`
  branch.
- `src/catraz/commands/run.py`:
  - `_oneoff_args(relpath, tty, sub, sub_args)` builds `run --rm --no-deps --build … <svc>
    <sub> -- <args>` (sub is `run` for claude, `exec` for shell). **Keep this function** —
    `tests/cli/test_shell.py` calls it directly.
  - `cmd_run` (39–56): ephemeral claude one-off; strips a leading `--`.
  - `cmd_shell` (59–71): ephemeral shell one-off (`sub="exec"`, default `bash`).
  - `_ensure_infra` lazy-starts infra for both.
- `src/catraz/cli.py`:
  - `run` subparser: `p_run.add_argument("claude_args", nargs=argparse.REMAINDER)` (no
    `parents=[_g()]`); `shell` subparser (121–122); `HANDLERS` has
    `"run": run_cmd.cmd_run`, `"shell": run_cmd.cmd_shell`.
  - re-export `from catraz.commands.run import _oneoff_args` (cli.py:33) — keep.
- The old daemon path (item 05's deleted `cmd_up` `--remote` branch) did: write auth
  fragment, security preflight, `_auto_sync_if_needed`, `assert_real_dirs`,
  `extra_env={"BASE_IMAGE": image.resolve_base(root)}`, `compose.prepare(render=True,
  extra_env=…)`, `assert_invariants`, `compose_run(["--profile","remote","up","-d", …])`,
  `_wait_healthy`, `_print_urls`. `claude-remote` reproduces this.

## Approach
Keep `claude_args` as `REMAINDER` (simple, matches today) and detect the mode **in code**:
if the first token is a known mode name, pop it; otherwise default to `claude`. This avoids
argparse `REMAINDER`-vs-positional ambiguity and mirrors the existing leading-`--` strip.
Dispatch: `claude`/`shell` → the existing ephemeral one-off paths (claude keeps item 03's
tee); `claude-remote` → a new `_start_remote_daemon` that ports item 05's daemon logic and
reuses the kept stack helpers.

## Steps
1. **run.py — mode parse + dispatch.** In `cmd_run`:
   - `MODES = ("claude", "claude-remote", "shell")`.
   - `raw = list(args.claude_args)`; if `raw and raw[0] in MODES`: `mode = raw.pop(0)` else `mode = "claude"`.
   - Strip one leading `--` from `raw` (so `run -- -p x`, `run claude -- -p x`, and
     `run -p x` all yield `["-p","x"]`).
   - `if mode == "claude-remote": return _start_remote_daemon(root, args, out)`.
   - Else build the ephemeral one-off: `sub = "exec" if mode == "shell" else "run"`;
     compute `relpath`/`tty` as today; `run_args = _oneoff_args(relpath, tty, sub, raw)`.
   - **Preserve item 03 (hard precondition):** item 03 must already be merged — verify
     `compose.run` accepts a `tee=` kwarg and `cmd_run` writes `.catraz/logs/agent/<ts>.log`
     **before** restructuring; if `tee=` doesn't exist yet, stop and flag the ordering
     violation rather than silently dropping the transcript. For `mode == "claude"` and
     **not** `tty`, keep the tee exactly as item 03 specified; `shell` is interactive → no
     tee (as item 03 already scoped). Keep the shared `_ensure_infra`/`assert_*`/
     `compose.prepare` preamble.
   - Delete `cmd_shell` (folded into dispatch). Factor the shared ephemeral preamble +
     run into a small `_run_oneoff(root, out, sub, raw, *, tee_if_claude)` helper if it
     reads cleanly; otherwise inline.
2. **run.py — `_start_remote_daemon(root, args, out)`** (ports item 05's removed
   `cmd_up --remote`): `assert_real_dirs`; `(root/".catraz").mkdir(exist_ok=True)`;
   `auth.write_auth_fragment(root)`; security preflight (`_security_preflight`);
   `_auto_sync_if_needed`; `extra_env={"BASE_IMAGE": image.resolve_base(root)}`;
   `prefix = compose.prepare(root, render=True, extra_env=extra_env)`;
   `assert_invariants(root, prefix=prefix)`;
   `compose_run(root, ["--profile","remote","up","-d"], prefix=prefix, check=False)`; on
   success `_wait_healthy(root, out, prefix=prefix)` then `_print_urls(out)`; return
   `r.returncode if r else EXIT_GENERAL` (the way the existing `run.py` code returns — do
   **not** reach for `_rc`, which `run.py` doesn't import).
   **Add the missing imports to `run.py`**: it currently imports `_security_preflight` and
   `_auto_sync_if_needed`, but **not** `auth` (it's `from catraz import auth`, the module —
   not from stack), `_wait_healthy`, or `_print_urls` (both
   `from catraz.commands.stack import …`). `image`, `compose`, `compose_run`,
   `assert_real_dirs`, `assert_invariants` are already imported.
3. **stack.py `_print_urls`** — re-add the daemon line, now pointing at
   `catraz run claude-remote` (item 05 dropped the `up --remote` line).
4. **cli.py** —
   - `run` subparser help → "run the sandbox: `run [claude|claude-remote|shell] -- <args>`
     (default mode: claude)". Keep `claude_args` REMAINDER.
   - Delete the `shell` subparser (121–122) and `"shell": run_cmd.cmd_shell` from HANDLERS.
   - Keep the `_oneoff_args` re-export.
5. **Tests** — `tests/cli/test_run_modes.py`:
   - Mode parse: `run` → `claude` + `[]`; `run claude-remote` → daemon; `run shell ls -la`
     → `shell` + `["ls","-la"]`; `run -p x` → `claude` + `["-p","x"]`; `run -- -p x` →
     `claude` + `["-p","x"]`; `run claude -- shell` → `claude` + `["shell"]`.
   - `claude-remote` dispatch: monkeypatch the collaborators **as bound in `run.py`'s
     namespace** — `run_cmd.compose_run`, `run_cmd.image.resolve_base`, and the stack
     helpers as imported into `run` (not `stack.compose_run`, which the deleted
     `test_up_profile.py` patched). Assert a `["--profile","remote","up","-d"]` call (profile
     before `up`) + `resolve_base` called.
   - `shell` mode: `_oneoff_args` called with `sub="exec"`.
   - `claude` non-TTY still tees (item 03 regression: a `tee` path under
     `.catraz/logs/agent` is passed; `shell`/`claude-remote` never tee).
   - `tests/cli/test_shell.py` (`_oneoff_args` direct tests) stays green — verify.
   - Grep for any remaining `"shell"` subcommand / `cmd_shell` references in tests and fix.
6. **README** — replace the `shell` subcommand mention with `catraz run shell`; add
   `catraz run claude-remote` as the Remote-Control daemon start (the line item 05 deferred);
   update the "Interactive mode" section to describe the modes.

## Success criteria
- `catraz run` and `catraz run claude -- -p "hi"` run the ephemeral agent (claude); non-TTY
  still writes a transcript (item 03).
- `catraz run shell` opens a shell; `catraz run shell -- ls` runs a command. The old
  `catraz shell` subcommand is gone (`parse_args(["shell"])` errors).
- `catraz run claude-remote` starts warden+squid+agent daemon, waits for health, prints
  URLs (the restored `up --remote`).
- `pytest tests/cli` green: `test_run_modes.py` added, `test_shell.py` unchanged, no
  dangling `cmd_shell`/`"shell"` refs.

## Risks & open questions
- Mode-name collision: a user wanting to pass a literal first arg equal to a mode name to
  claude must use `catraz run claude -- <arg>` (or `run -- <arg>`). Documented; the bare
  first token is the mode. Acceptable for three reserved words.
- Coupling: this plan consumes item 05's kept helpers and item 03's tee. Execution order
  must be 03 → 05 → 07. If 05's helpers were removed, `_start_remote_daemon` can still be
  written self-contained, but reuse is preferred.
- `claude-remote` is a daemon (`up -d`, restart unless-stopped) while `claude`/`shell` are
  ephemeral (`run --rm`) — the mode name encodes the lifecycle difference the old
  `run` vs `up --remote` split hid (the original P2 motivation).
- **Pre-existing limitation (don't "fix" it):** the `run` subparser uses
  `nargs=REMAINDER` with **no** `parents=[_g()]`, so global flags after the verb
  (`catraz run -C /p claude-remote`) land in `claude_args`. This is unchanged from today;
  the working form is `catraz -C /p run claude-remote` (top parser carries the globals).
  Adding `_g()` would break REMAINDER — leave it.

## Revision history
- v0: initial draft
- v1 (roast, shared agent): verdict mergeable; mode parsing verified correct for all six
  invocations, daemon port faithful, 03/05 dependencies confirmed. Accepted: name the
  imports `run.py` is missing (`auth`, `_wait_healthy`, `_print_urls`; return via
  `r.returncode`, not `_rc`); add the `.catraz` mkdir line; make item-03's tee a hard
  precondition (abort if `tee=` not yet in tree); fix the test monkeypatch targets to
  `run_cmd.*` namespace; documented the pre-existing REMAINDER/`_g()` limitation so nobody
  "fixes" it.
