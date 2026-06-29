# Wave 02 — Rewrite `cmd_init` as the mode-aware wizard

> Part of init-flow/01. Runs **alone in wave 02** (after all of wave 01).
> Depends on `01-ui-interaction-helpers` (the `Out` helpers it calls) and on
> `01-doctor-mode-aware` — `cmd_init` runs `run_doctor()` as its preflight
> (`setup.py:190`) and returns `EXIT_DOCTOR` on any `bad`, so the wizard's
> "off/read-only produces a config doctor passes" tests are red until doctor is
> mode-aware (roast iter-1 #4). It does **not** import the warden package; it only
> relies on the `GITLAB_MODE` `.env` convention defined in `00-overview`.

## Goal
Replace the current always-asks-everything `cmd_init` with a wizard that asks
`AUTH_MODE`, then a single GitLab-mode question, then only the questions that mode
needs — each with a sensible default taken via one Enter. Persist `GITLAB_MODE`
to `.env` and policy (`allowed_projects`, `branch_prefix`) to its SSOT,
`warden.toml`. Keep `--yes` fully non-interactive.

## Context / constraints
- `src/catraz/commands/setup.py::cmd_init` is the target. It already: makes dirs
  via `_doctor_fix`, copies config templates + `.env.example`, sets `DEV_UID`,
  ensures `.catraz/secrets/`, runs sync, ensures `.gitignore`, runs `doctor`.
  Preserve all of that; only the prompting block (lines ~60–161) is rewritten.
- Secret files: `.catraz/secrets/{gitlab_read_token,gitlab_write_token,
  anthropic_api_key}`, lowercase of the env var, mode 0600 (see `doctor.SECRETS`).
- Policy SSOT is `warden.toml` (`branch_prefix`, `allowed_projects`); `.env`
  `WARDEN_*` are *overrides*. `_resolve_allowed_projects` reads env-override-then-
  toml. `validate_project` validates one entry.
- `.env` writes go through `set_env_values(env_path, {...})`.
- `args` has `yes`, `force`, `skip_sync`.

## Approach
Drive the wizard off `GITLAB_MODE`. Use the new `Out.ask/confirm/choice/secret`.
Write `GITLAB_MODE` + `AUTH_MODE` + `GITLAB_URL` to `.env`; write
`allowed_projects` + `branch_prefix` to `warden.toml` via a small regex setter;
write secrets to files. Factor the interactive and `--yes` paths through one
shared "resolve settings" step so both produce the same `.env`/toml/secret writes.

## Steps

1. **TOML setters (new, in `catraz/policy.py`** — next to the existing
   `_read_toml_allowed_projects`; do not invent a new module (roast iter-1 #8)).
   Add:
   ```python
   def set_toml_scalar(path, key, value): ...   # branch_prefix = "claude/"
   def set_toml_list(path, key, values): ...     # allowed_projects = ["g/p", ...]
   ```
   Implementation must match the **actual shipped `warden.toml`**, not an idealized
   one (roast iter-1 #6):
   - The shipped lines carry **inline comments** that must survive, e.g.
     `branch_prefix       = "claude/"          # R2: only branches…` (line 13) and
     padded alignment. Use a regex that replaces only the value, preserving the
     trailing `# …` comment and leading whitespace/alignment:
     `^(?P<pre>\s*KEY\s*=\s*)(?P<val>"[^"]*"|\[[^\]]*\])(?P<post>\s*(#.*)?)$`,
     `re.M` → rewrite group `val`, keep `pre`/`post`.
   - The shipped `allowed_projects` value is `[""]` (a list with one empty string,
     line 20), **not** `[]` — the regex's `\[[^\]]*\]` must match that exact form.
   - Quote strings/list items with `json.dumps` (valid TOML for simple strings and
     lists of strings).
   - If the key is genuinely absent, append a new line.
   Tests must run against a **copy of the real shipped template**
   (`src/catraz/assets/config/warden.toml`), asserting the inline comment is intact
   after the write and that `_read_toml_allowed_projects` reads back the new value.

2. **AUTH_MODE prompt.** Before the GitLab block:
   ```python
   auth_mode = env.get("AUTH_MODE") or "subscription"
   if not args.yes and (args.force or "AUTH_MODE" not in env):
       auth_mode = out.choice("Claude auth mode?",
           [("subscription","subscription — import host ~/.claude (default)"),
            ("api_key","api_key — dedicated Anthropic API key")],
           default=0 if auth_mode=="subscription" else 1)
   updates["AUTH_MODE"] = auth_mode
   ```

3. **GitLab mode prompt (the heart).**
   ```python
   cur_mode = env.get("GITLAB_MODE") or "read-write"
   if args.yes:
       mode = _yes_gitlab_mode(env)          # see step 7
   else:
       mode = out.choice(
         "GitLab integration?",
         [("read-write","read-write — read + push (needs read & write tokens)"),
          ("read-only","read-only — read only (needs a read token)"),
          ("off","off — no GitLab (the agent can't talk to GitLab)")],
         default={"read-write":0,"read-only":1,"off":2}[cur_mode])
   updates["GITLAB_MODE"] = mode
   ```

4. **Tokens — only what the mode needs.** When `mode != "off"` and not `--yes`:
   - `GITLAB_URL`: `url = out.ask("GitLab base URL (set for self-hosted)", env.get("GITLAB_URL") or "https://gitlab.com")`; record in `updates`.
   - read token (always for non-off):
     `_write_secret(secrets_dir, "gitlab_read_token", out.secret("GitLab READ token (read_api, read_repository)", current=<existing unless --force>))`.
   - write token **only when `mode == "read-write"`**:
     `out.secret("GitLab WRITE token (api scope)", current=...)`.
   - When `mode != "read-write"`, **ensure** `gitlab_write_token` exists (the
     compose mount needs the file) but **do not overwrite a non-empty existing
     one** — `if not p.exists(): write ""` (roast iter-2 #3). Switching
     `read-write → read-only`/`off` must not silently destroy a valid token the
     user may want back later; doctor already warns about a stray write token in
     read-only (01-doctor step 3), so keeping it is safe. Never prompt for it.
     This is the concrete implementation of requirement 2.
   When `mode == "off"`: ensure `gitlab_read_token` and `gitlab_write_token` exist
   (so the secret mounts exist) using the same non-destructive ensure-only rule;
   prompt for neither.

5. **Policy — only when `mode != "off"` (requirement 3).**
   - `allowed_projects`: reuse `_resolve_allowed_projects`; if already set and not
     `--force`, keep. Else prompt with guidance (full `group/sub/project` paths,
     comma-separated, no wildcards), validate each via `validate_project`, and
     write the valid list to **`warden.toml`** via `set_toml_list`. If the user
     leaves it empty in a non-off mode, warn that the warden is fail-closed and the
     stack won't start until it's set (do not silently continue as if fine).
   - `branch_prefix` ("allowed branches", requirement 3 + 4): prompt
     `out.ask("Branch prefix the agent may push to", current or "claude/")`; write
     to `warden.toml` via `set_toml_scalar`. Default `claude/`, one Enter to accept.
   - **Do not** prompt for quotas (`max_open_*`, `max_writes_per_hour`): they have
     safe defaults in `warden.toml` and live there as SSOT; prompting all of them
     would violate "minimal input". Mention in the final summary that they're
     editable in `warden.toml`.
   - **Migrate stale `.env` overrides (roast iter-1 #7).** Because the env var
     `WARDEN_ALLOWED_PROJECTS`/`WARDEN_BRANCH_PREFIX` **wins over** `warden.toml`
     in both `_resolve_allowed_projects` (`policy.py:25-31`) and the warden
     (`config.py`, compose `ALLOWED_PROJECTS=${WARDEN_ALLOWED_PROJECTS:-}`), and
     the *old* `cmd_init` wrote `WARDEN_ALLOWED_PROJECTS` into `.env`
     (`setup.py:157`), a re-run would leave a stale `.env` override silently
     shadowing the value we now write to `warden.toml`. After writing policy to
     toml, **delete** `WARDEN_ALLOWED_PROJECTS` and `WARDEN_BRANCH_PREFIX` from
     `.env`. This needs a new `unset_env_keys(path, keys)` in
     `catraz/envfile.py` (sibling to `set_env_values`); add it with a unit test.

6. **anthropic_api_key — only when `auth_mode == "api_key"`.** Prompt via
   `out.secret(...)` (interactive) or `ANTHROPIC_API_KEY` env (`--yes`); write to
   `.catraz/secrets/anthropic_api_key`. In `subscription` mode, never prompt; if a
   stale `anthropic_api_key` file exists, leave it (doctor handles the conflict).

7. **`--yes` path (`_yes_gitlab_mode(env)` helper).** Non-interactive resolution:
   - If `GITLAB_MODE` env set → use it (validate ∈ off|read-only|read-write).
   - Else infer: `GITLAB_READ_TOKEN` set & `GITLAB_WRITE_TOKEN` set → `read-write`;
     read set only → `read-only`; neither → `off`.
   Then write secrets from the `*_TOKEN` / `ANTHROPIC_API_KEY` env vars exactly as
   today (empty files where absent), write `GITLAB_URL` if provided, and write
   `WARDEN_ALLOWED_PROJECTS` (if provided) to `warden.toml` via `set_toml_list`
   (not `.env`), and `WARDEN_BRANCH_PREFIX` to `warden.toml` if provided. Keep the
   existing per-project `validate_project` filtering and warnings.

8. **Final summary.** After writes, print a short recap via `out.info`: chosen
   `AUTH_MODE`, `GITLAB_MODE`, GitLab URL (if any), #allowed projects, branch
   prefix, and "edit quotas in .catraz/config/warden.toml". Then the existing
   `doctor` preflight block runs unchanged.

9. **`.env.example` fix (requirement 5).** In `src/catraz/assets/.env.example`:
   - Add a documented `GITLAB_MODE=read-write   # off | read-only | read-write`
     line in the Wiring section.
   - Resolve the contradiction: the header says "WARDEN_* env vars have been
     removed" but the file still lists them commented and compose still reads them.
     Keep them as documented *optional overrides* (compose reads them) and correct
     the header wording to "WARDEN_* are optional overrides of warden.toml; the
     SSOT is warden.toml".

## Tests (`tests/cli/test_secrets.py` + new `tests/cli/test_init_wizard.py`)
- Update `_yes_args()` fixtures if needed; existing `--yes` tests must still pass
  (now also asserting `GITLAB_MODE` is written).
- `--yes` inference: no token env → `.env` has `GITLAB_MODE=off`, both token files
  exist and are empty, no `allowed_projects`/`branch_prefix` prompt occurred.
- `--yes` with `GITLAB_READ_TOKEN` only → `GITLAB_MODE=read-only`; write token file
  empty; `WARDEN_ALLOWED_PROJECTS` env lands in `warden.toml`.
- `--yes` with both tokens → `GITLAB_MODE=read-write`.
- Interactive (monkeypatch `input`/`getpass`, `Out(color=False)`):
  - choosing `off` writes `GITLAB_MODE=off`, never calls `getpass`, writes no
    `allowed_projects`/`branch_prefix`.
  - choosing `read-only` prompts read token only, writes branch_prefix default
    `claude/` and the typed projects to `warden.toml`.
  - choosing `read-write` prompts both tokens.
  - Enter-on-default selects `read-write` (cur_mode default) and `claude/`.
- `set_toml_scalar`/`set_toml_list` round-trip via `_read_toml_allowed_projects`.

## Success criteria
- The three modes each produce a config that `doctor` (post wave-02-doctor) passes
  and `catraz up` would accept (warden no longer aborts for off/read-only).
- No prompt appears that the chosen mode doesn't need.
- `pytest tests/cli` green.

## Revision history
- v0: initial draft
- v1 (roast iter-1): TOML setter location fixed to `policy.py`; setter spec
  hardened for inline comments + `[""]`; stale `.env` override migration via new
  `unset_env_keys`; re-waved (depends on mode-aware doctor preflight) (#6,#7,#8,#4).
- v2 (roast iter-2): token-file writes made non-destructive (ensure-exists, never
  clobber a non-empty token on mode downgrade) (#3).
