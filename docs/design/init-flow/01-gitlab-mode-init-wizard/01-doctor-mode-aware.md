# Wave 01 — Make `doctor` GITLAB_MODE-aware

> Part of init-flow/01. Runs in wave 01 (parallel with 01-mode-enforcement and
> 01-ui-interaction-helpers). It only depends on the `GITLAB_MODE` `.env`
> convention from `00-overview`, not on the warden package. It must land **before**
> `02-init-wizard`, because `cmd_init` runs `run_doctor()` as its preflight
> (`setup.py:190`) — an un-mode-aware doctor would fail the wizard's off/read-only
> tests (roast iter-1 #4).

## Goal
Stop `doctor` from reporting an intentionally-off or read-only GitLab as broken.
The checks must match the same three-mode contract the warden enforces, so a
config the warden accepts also passes `doctor`, and one it rejects fails `doctor`.

## Context / constraints
- `src/catraz/doctor.py`:
  - `check_gitlab(env, f)` — warns if `GITLAB_URL` unset.
  - `check_tokens(root, env, f)` — flags **each** empty token in `SECRETS` as
    `bad`, then `_probe_gitlab_tokens` does an online probe.
  - `check_policy(root, env, f)` — flags empty `allowed_projects` as `bad`
    (fail-closed) — true only when GitLab is on.
  - `check_auth` — subscription/api_key consistency (already mode-aware for AUTH).
  - `run_doctor` calls them per section.
- Mode lives in `.env`: `mode = env.get("GITLAB_MODE") or "read-write"`.

## Approach
Read `GITLAB_MODE` once at the top of each GitLab-related check and branch the
severity. Keep the online probe, but only run it for the tokens a mode actually
uses.

## Steps

1. **Helper.** Add `def _gitlab_mode(env): return (env.get("GITLAB_MODE") or "read-write").strip()`
   in `doctor.py`.

2. **`check_gitlab`.** If `mode == "off"`: emit `f.ok("tokens", "GitLab disabled (GITLAB_MODE=off)")`
   and return — don't nag about `GITLAB_URL`. Else keep current behaviour.

3. **`check_tokens`.** Branch on mode:
   - `off`: `f.ok("tokens", "GitLab off — tokens not required")`; return (no probe).
   - `read-only`: require `gitlab_read_token` non-empty (`bad` if empty); the write
     token is expected empty — if a write token *is* present, `f.warn` ("write token
     set but GITLAB_MODE=read-only — it will be ignored"). Probe only the read token.
   - `read-write`: current behaviour (both required, probe both).
   Refactor `_probe_gitlab_tokens` to accept which tokens to probe (e.g. a list of
   `(label, filename)`), instead of always probing both. The existing scope checks
   (read carries `api` → warn; write lacks `api` → bad) stay, applied per probed
   token.

4. **`check_policy`.** If `mode == "off"`: empty `allowed_projects` is fine —
   `f.ok("policy", "GitLab off — allowlist not required")` and return. Else keep the
   current fail-closed `bad` on empty + `validate_project` checks.

5. **`check_auth`.** No mode change needed (it keys off `AUTH_MODE`), but verify it
   still passes when GitLab is off (it should — independent axis).

## Tests (`tests/cli/test_doctor_gitlab.py` + as needed)
- `off` mode with both token files empty and empty `allowed_projects`:
  `run_doctor` yields **no** `bad` findings from `tokens`/`policy`.
- `read-only` mode with a read token, empty write token, non-empty allowlist:
  no `bad`; a present write token yields the "ignored" warn.
- `read-write` mode unchanged: empty token(s) / empty allowlist still `bad`
  (preserve existing assertions, now under explicit `GITLAB_MODE=read-write`).
- Probe is not attempted in `off` mode (monkeypatch `_gitlab_get` to raise if
  called).

## Success criteria
- `doctor` verdicts line up with `warden._validate` for all three modes: a setup
  the warden would start passes `doctor`; one it would reject fails `doctor`.
- `pytest tests/cli/test_doctor_gitlab.py` green.

## Revision history
- v0: initial draft
- v1 (roast iter-1): re-waved to wave 01 (must precede the init wizard, which runs
  `doctor` as preflight) (#4).
