# Wave 01 — Enforce GITLAB_MODE in the warden AND the agent

> Part of init-flow/01. Runs in wave 01 (parallel with 01-ui-interaction-helpers
> and 01-doctor-mode-aware). **Sole owner of `docker-compose.yml`** in this plan,
> so no two subagents edit that file concurrently.

## Goal
Make `off` and `read-only` real, fail-closed configurations — not just unprompted
ones. The warden must (a) accept these modes without aborting, (b) **never send a
write token upstream when writes are disabled**, (c) deny *all* GitLab ops in
`off`. The agent must stop pretending GitLab works when it's `off`.

## Why a guard in `decide` is NOT enough (roast iter-1 #1 + iter-2 #1, both verified)
The warden has **two** enforcement entry points: `policy.decide` (used by
`receive_pack` and the REST API) and the git **read** handlers that deliberately
bypass `decide`. Mode-gating only `decide` leaves real holes. Token is dereferenced
outside `decide` in these paths:
- `warden/warden/context.py:48` `resolve_service_account()` → `get_json("user", TokenKind.WRITE)`,
  run at **startup** (`__main__.py:39`) and in **every** `reconcile()`
  (`context.py:85`, periodic via `__main__.py:25`).
- `api_proxy.py:100-102` calls `ctx.mr_owned_by_claude(...)` (→ `resolve_service_account`)
  **before** `decide` at line 105.
- `git_proxy.py:40 advertise()` and `git_proxy.py:70 upload_pack()` call only
  `project_gate`, then stream upstream — **no `decide`**. `advertise` picks the
  token via `_service_token` (`git_proxy.py:36-37`), which returns **WRITE** for
  `service=git-receive-pack`. So a `read-only` push begins with
  `GET info/refs?service=git-receive-pack` → `git_get(..., token=WRITE)` with the
  empty write token *before* `receive_pack`'s `decide` ever denies it; and an `off`
  clone streams with the empty READ token, undenied.
So read-only/off would ship an empty/wrong token upstream on boot, on every MR
request, **and on git clone/fetch/push discovery** regardless of any `decide`
guard. We must gate the *token use* at every one of these sites.

## Steps — Warden config (`warden/warden/config.py`)

1. Add `gitlab_mode: str = "read-write"` to the `Config` dataclass, plus:
   ```python
   @property
   def gitlab_enabled(self) -> bool: return self.gitlab_mode != "off"
   @property
   def writes_enabled(self) -> bool: return self.gitlab_mode == "read-write"
   ```
2. In `from_env`, set `gitlab_mode=(env.get("GITLAB_MODE") or "read-write").strip()`.
3. Rewrite `_validate(cfg)` to branch on mode (keep the
   `ConfigError("invalid configuration: " + "; ".join(problems))` shape):
   - Always: `gitlab_mode ∈ {off,read-only,read-write}` else a problem; quotas
     `> 0` (unchanged).
   - `off`: no token / allowlist / branch_prefix requirement.
   - `read-only`: require `read_token`, non-empty `allowed_projects`, non-empty
     `branch_prefix`. **No** write_token requirement.
   - `read-write`: read_token **and** write_token, non-empty allowlist + prefix
     (today's behaviour).

## Steps — Warden enforcement (deny + token-use gates)

4. **Read deny-all for `off`** (roast #2): at the very top of
   `warden/warden/policy.py::decide`, before channel dispatch:
   ```python
   if not cfg.gitlab_enabled:
       return Decision(False, "R0", "GitLab disabled (GITLAB_MODE=off)")
   ```
   Add `R0` to the rule docstring/table comment.
5. **Write deny for read-only/off** (roast #5, one guard — `decide` already has
   `cfg`): immediately after the deny-all gate, OR as a single check applied to
   the final decision, deny any write verdict:
   ```python
   # after computing `d` in decide(), or guard each write-return site once:
   if d.allow and d.token == TokenKind.WRITE and not cfg.writes_enabled:
       return Decision(False, "R0", f"writes disabled (GITLAB_MODE={cfg.gitlab_mode})")
   ```
   Prefer wrapping the single `decide` return so it covers both `_decide_git`
   (`policy.py:89,108`) and `_decide_api` (`policy.py:131`) write paths at once.
   Do **not** scatter a copy into each R2/R3/R5 site.
6. **Token-use gate at `resolve_service_account`** (`warden/warden/context.py`):
   ```python
   async def resolve_service_account(self):
       if not self.cfg.writes_enabled:
           return None          # never send the (possibly empty) write token
       ... existing body ...
   ```
   Confirm downstream `Optional[int]` consumers already tolerate `None`:
   `mr_owned_by_claude` (`context.py:59`) and `_list_claude_mrs` (`context.py:142`)
   already take `Optional`. Where a `None` SA would make an ownership check
   *pass*, ensure it instead makes it fail-closed (ownership unknown ⇒ deny) — the
   write is denied anyway by step 5, but keep the ownership default safe.
7. **Reconcile under each mode** (`context.py:83 reconcile`):
   - `off` (`not gitlab_enabled`): skip the upstream reconcile entirely; mark state
     reconciled/unlocked so the warden serves (and then denies) without ever
     calling GitLab. Both tokens are empty in `off`; no upstream call may happen.
   - `read-only`: `resolve_service_account` returns `None` (step 6); the branch/MR
     listing uses the **READ** token (`_resolve_project_id`/`_get_paginated` already
     use `TokenKind.READ`, `context.py:110,125`) — harmless and lets quotas display.
   - `read-write`: unchanged.
8. **MR-ownership pre-fetch** (`api_proxy.py:100-102`): guard it with
   `if ctx.cfg.writes_enabled and ep is not None and mr_owned_by_claude in ep.checks:`
   so read-only/off never trigger the write-token `get_json("user", WRITE)` before
   `decide` denies the write.

9. **Git read handlers** (`warden/warden/git_proxy.py`, roast iter-2 #1 — this file
   is added to this task's owned set). `advertise()` (line 40) and `upload_pack()`
   (line 70) never call `decide`, so they need their own mode gates, placed right
   after the existing `project_gate` check (mirror the `project_gate`/`deny_json`
   shape; reuse `Decision(False, "R0", …)` + `deny_json`):
   - **Both handlers, `off`:** `if not ctx.cfg.gitlab_enabled: return deny_json(Decision(False, "R0", "GitLab disabled (GITLAB_MODE=off)"))`.
     This closes the `off` clone/fetch read hole.
   - **`advertise`, push discovery:** when
     `_service_token(service) == TokenKind.WRITE and not ctx.cfg.writes_enabled`,
     `return deny_json(Decision(False, "R0", f"writes disabled (GITLAB_MODE={ctx.cfg.gitlab_mode})"))`
     **before** the `git_get` call — so the empty write token is never sent during a
     read-only push's `info/refs?service=git-receive-pack`.
   - `upload_pack` (clone/fetch body) stays allowed in `read-only`/`read-write`
     (it already uses the READ token); only the `off` gate above applies to it.

## Steps — Agent-side routing (roast iter-1 #3, verified)

10. **Compose: pass the mode to the agent.** In
    `src/catraz/assets/compose/docker-compose.yml`, add to the **agent**
    (`claude-dev-env`) `environment:` block (near the existing `GITLAB_URL` line):
    `- GITLAB_MODE=${GITLAB_MODE:-read-write}`.
11. **Compose: pass the mode to the warden.** Add to the **gitlab-warden**
    `environment:` block: `- GITLAB_MODE=${GITLAB_MODE:-read-write}`. (Token
    secrets stay as `*_FILE` mounts; in off/read-only the corresponding file is
    just empty — the mount still exists, no compose-shape change.)
12. **Entrypoint: gate the `insteadOf` rewrite** in
    `src/catraz/assets/container/entrypoint.py` (the function at ~line 117). When
    `os.environ.get("GITLAB_MODE","read-write") == "off"`, **skip** installing the
    `url.<warden>.insteadOf <gitlab>` rewrite and print a one-line notice
    ("GitLab disabled (GITLAB_MODE=off) — git will not be routed to the warden").
    For `read-only`/`read-write` the rewrite is installed as today (the warden
    enforces read-vs-write; the agent doesn't need to distinguish those two).

## Invariants / no-op confirmations
- `compose.assert_invariants` must still pass: the agent carries **no**
  `GITLAB_*_TOKEN` (we add only `GITLAB_MODE`, which is not a token) — re-run the
  invariant test.
- **Squid is a non-issue** (roast #9): the warden reaches GitLab over `egress-net`
  (not via Squid) and the agent's GitLab traffic is `no_proxy`'d straight to the
  warden (`docker-compose.yml:125`). No `squid.conf`/`allowlist.txt` change for any
  mode.

## Tests
- `warden/tests/test_config.py`:
  - Re-express `test_missing_tokens_abort_startup` / `test_empty_allowlist_aborts_startup`
    as holding **only** under `GITLAB_MODE=read-write` (default).
  - `test_off_mode_allows_empty_tokens_and_allowlist` — builds, no raise.
  - `test_read_only_requires_read_token_not_write` — read token + allowlist (+
    prefix) builds with no write token; missing read token raises.
  - `test_invalid_mode_aborts` — `GITLAB_MODE=nonsense` raises.
- `warden/tests/test_policy.py`:
  - `test_off_denies_reads_and_writes` — any GET/push denied with reason R0 when
    `gitlab_mode="off"`.
  - `test_read_only_denies_writes_allows_reads` — push/MR-create denied (R0), GET
    allowed, under `gitlab_mode="read-only"`.
- New `warden/tests/test_context.py` (or extend existing): with a fake upstream
  that records calls, assert `resolve_service_account()` makes **no** upstream call
  when `writes_enabled` is False, and `reconcile()` makes **no** upstream call in
  `off`.
- `warden/tests/test_git_proxy.py` (extend; this is the regression the iter-1 test
  set would have missed): with a fake upstream that records calls,
  - `off`: `advertise(service=git-upload-pack)` and `upload_pack` both return a
    deny (R0) and make **no** upstream call.
  - `read-only`: `advertise(service=git-receive-pack)` returns a deny (R0) and makes
    **no** `git_get` call (the empty write token is never sent);
    `advertise(service=git-upload-pack)`/`upload_pack` still pass through with READ.
  - `read-write`: unchanged.
- `tests/cli/test_invariants.py` (or wherever `assert_invariants` is exercised):
  agent still has no `GITLAB_*_TOKEN` after adding `GITLAB_MODE`.

## Success criteria
- `from_env` builds valid configs for all three modes; aborts only on genuine
  misconfiguration within a mode.
- In `off`/`read-only` the warden provably makes no write-token upstream call
  (test #context), and denies the right operations (test #policy).
- In `off` the agent does not route git to the warden.
- `cd warden && pytest` and `pytest tests/cli/test_invariants.py` green.

## Owned files
`warden/warden/{config,policy,context,api_proxy,git_proxy}.py`,
`warden/tests/{test_config,test_policy,test_context,test_git_proxy}.py`,
`src/catraz/assets/compose/docker-compose.yml`,
`src/catraz/assets/container/entrypoint.py`. Disjoint from the doctor and ui
wave-01 tasks, so all three run in parallel without merge conflicts.

## Revision history
- v0: split out of the old single warden file; added token-use gates, off
  deny-all, agent routing, single compose owner (roast iter-1 #1,#2,#3,#5,#9).
- v1 (roast iter-2): added `git_proxy.advertise`/`upload_pack` mode gates — the
  read channel bypassed `decide`, re-opening the empty-token hole on `off` clone and
  `read-only` push discovery; added `git_proxy.py` to owned files + `test_git_proxy`
  (iter-2 #1,#2,#7).
