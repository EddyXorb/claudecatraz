# Fix the init flow: a GitLab-mode-aware setup wizard

> Status: draft · Topic: init-flow · Iterations planned: 2

## Goal
Make `catraz init` a coherent wizard whose every question has a sensible default
offered through a one-keystroke interaction, and where **GitLab is optional**.
A user must be able to (a) skip GitLab entirely and still bring the stack up,
(b) give only a read token (read-only GitLab), or (c) give both tokens
(read-write). The wizard, `.env`, compose, the warden, and `doctor` must all
agree on which of those three modes is active.

## The core idea: `GITLAB_MODE`
Introduce one explicit, persisted setting — `GITLAB_MODE` in `.catraz/.env` —
with three values:

| mode | tokens required | allowlist / branch_prefix | warden behaviour |
|---|---|---|---|
| `off` | none | not required | starts; every GitLab op denied (no read/write) |
| `read-only` | read token | required | reads allowed; **all writes denied** |
| `read-write` | read + write token | required | full current behaviour |

`GITLAB_MODE` is wiring/infra, so it lives in `.env` next to `AUTH_MODE`
(not in `warden.toml`, which holds policy tunables). It is the single signal that
lets the warden safely relax its fail-closed validation: today the warden cannot
tell "deliberately off" from "misconfigured read-write", so it aborts. With an
explicit mode it can.

## Why this is the whole fix, not just UI
`warden/warden/config.py::_validate` (production path, `strict=True`) currently
*aborts startup* unless **both** tokens are set and `allowed_projects` is non-empty
(see `warden/tests/test_config.py` `test_missing_tokens_abort_startup`,
`test_empty_allowlist_aborts_startup`). (`branch_prefix` also must be non-empty,
but it defaults to `claude/` in `config.py:32` and the shipped `warden.toml`, so it
only bites if a user blanks it — it is not what breaks a fresh init.) So a prettier
wizard that lets you skip a token would just produce a stack that crashes on
`catraz up`. The fix spans the wizard, the warden's validation **and** its
write-token use, the agent's git routing, and doctor.

## Requirements traceability (from the request)
1. **Tokens skippable, asked explicitly** → `GITLAB_MODE=off`, offered as the
   first GitLab question. (02-init-wizard, 01-mode-enforcement)
2. **Read token alone is OK** → `GITLAB_MODE=read-only`; write token not prompted
   and not required by the warden — and the warden never *uses* it. (02-init-wizard,
   01-mode-enforcement)
3. **Allowed branches/projects asked only when there are tokens, with sensible
   defaults** → `allowed_projects` and `branch_prefix` (default `claude/`) are
   prompted only when mode ≠ off. (02-init-wizard)
4. **Every question with a sensible default offers it via simple interaction** →
   new `Out.ask/confirm/secret/choice` helpers; `[default]` accepted with Enter.
   (01-ui-interaction-helpers, 02-init-wizard)
5. **Explore further / introduce improvements** → fix the `.env.example` ↔ compose
   contradiction about `WARDEN_*`; write policy to its documented SSOT
   (`warden.toml`) and migrate stale `.env` overrides; make `doctor` mode-aware so
   it stops flagging an intentionally-off GitLab as broken; make the **agent**
   stop routing git to the warden when GitLab is off; prompt `AUTH_MODE` and
   `anthropic_api_key` in the wizard instead of requiring a hand-edited `.env`
   before first run. (01-doctor-mode-aware, 01-mode-enforcement, 02-init-wizard)

## Execution waves
Lower prefix runs first; same prefix may run in parallel. Files were re-waved
after roast iteration 1 (#4): `cmd_init` runs `doctor` as its preflight, so init
must come *after* a mode-aware doctor; and the three wave-01 tasks edit disjoint
files (`01-mode-enforcement` is the sole owner of `docker-compose.yml`), so they
parallelize without merge conflicts.

- **Wave 01 (parallel — disjoint files):**
  - `01-mode-enforcement.md` — `GITLAB_MODE` in warden `Config`; mode-aware
    `_validate`; **deny-all reads in `off`** (REST **and** the git read handlers);
    **gate write-token *use*** at every site that bypasses `decide` —
    `resolve_service_account`/`reconcile`/MR-ownership **and**
    `git_proxy.advertise`/`upload_pack`; gate the agent's `insteadOf` rewrite in
    `off`; all `docker-compose.yml` env passthrough; warden + invariant tests.
  - `01-doctor-mode-aware.md` — make `check_gitlab/tokens/policy` respect
    `GITLAB_MODE`; doctor tests.
  - `01-ui-interaction-helpers.md` — `Out.ask/confirm/secret/choice` with
    default-on-Enter and non-TTY handling; ui tests.
- **Wave 02 (after wave 01):**
  - `02-init-wizard.md` — rewrite `cmd_init` as the mode-aware wizard
    (interactive + `--yes`); write policy to `warden.toml` and migrate stale
    `.env` overrides; fix `.env.example`. Depends on the UI helpers and on the
    mode-aware doctor (preflight).

## Success criteria
- `catraz init` (interactive) can produce a working `off`, `read-only`, or
  `read-write` setup; `catraz up`/`doctor` succeed in all three (no warden abort
  in `off`/`read-only`).
- Every wizard prompt with a default accepts Enter to take it.
- `doctor` reports `off` GitLab as OK/informational, not `bad`.
- `pytest` green across `tests/cli`, `warden/tests`, plus new tests.

## Risks & open questions
- **TOML editing** for `allowed_projects`/`branch_prefix`: no stdlib TOML writer.
  Decided in 02-init-wizard: targeted regex replace of the two known shipped lines,
  **preserving their inline comments and alignment**, tested against the real
  shipped `warden.toml` (whose `allowed_projects` is `[""]`, not `[]`). This is the
  documented SSOT and beats hiding policy in `.env`.
- **read-only / off enforcement must gate token *use* at every site that bypasses
  `decide`** (roast iter-1 #1 + iter-2 #1): the write token is sent upstream by
  `resolve_service_account` (startup + every `reconcile`), the pre-`decide`
  MR-ownership lookup, **and `git_proxy.advertise`** (push discovery uses the WRITE
  token); the git read handlers (`advertise`/`upload_pack`) bypass `decide`
  entirely. All are gated on `writes_enabled`/`gitlab_enabled` in
  01-mode-enforcement; `off` also gets a read deny-all (REST + git) so it doesn't
  401 on an empty read token.
- **Squid is a non-issue** (roast iter-1 #9): the warden talks to GitLab over
  `egress-net` (not Squid) and the agent's GitLab traffic is `no_proxy`'d straight
  to the warden, so no `squid.conf`/`allowlist.txt` change is needed for any mode.
- **Existing tests** asserting unconditional abort (`test_missing_tokens_abort_startup`
  etc.) must be re-expressed as "abort in read-write mode" — they encode the old
  contract and will change.

## Revision history
- v0: initial draft
- v1 (roast iter-1): split the warden file into `01-mode-enforcement` (now also
  gates write-token *use* and the agent `insteadOf` routing, owns all compose
  edits); added `off` read deny-all; re-waved so doctor precedes init; hardened the
  TOML setter spec (inline comments, `[""]`); added stale-`.env`-override migration
  via a new `unset_env_keys`; fixed the abort-condition wording; noted squid is a
  non-issue. Accepted all 10 findings (3 verified against code); rejected none.
- v2 (roast iter-2): closed the git read-channel hole — `git_proxy.advertise`/
  `upload_pack` bypass `decide`, so `off` clone and `read-only` push discovery still
  shipped empty tokens; added their mode gates + `git_proxy.py`/`test_git_proxy` to
  01-mode-enforcement; made wizard token writes non-destructive on mode downgrade;
  dropped unused `Out.confirm`; bounded `Out.choice`. Accepted all 7 findings (2
  verified against code); rejected none.
