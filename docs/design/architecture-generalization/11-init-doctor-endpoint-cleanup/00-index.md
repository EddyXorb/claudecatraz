# 11 — Init/doctor/credential-modes cleanup — implementation steps

These derive from the main document
[`../11-init-doctor-endpoint-cleanup.md`](../11-init-doctor-endpoint-cleanup.md)
(the *what/why*). On contradiction the main document wins — report it, do not
guess.

## Order and progress

The number is the dependency level: same number = independent (parallelizable,
separate commit); a higher number requires all lower ones. Flip the Status
column in the same commit as the step.

| Level | Step | Purpose | Status |
| --- | --- | --- | --- |
| 01 | operator-surface | init wizard + doctor + `warden.toml` template + `.env.example` onto grouped host-keyed tokens and auto-synthesised `[[git.endpoint]]`; remove `GITLAB_MODE`/`GITLAB_URL`; sweep old secret filenames; update/extend tests | ✅ a33089a |
| 01 | credential-modes | `persistent` = full `.catraz/state/<profile>/` bind (delete the selective-symlink wiring), `sync` = tmpfs (unchanged); compose overlay per `credentials.mode`; `prepare_home` merges instead of clobbering; update tests | ✅ 75daf28 |

The two steps touch disjoint files and may be done in either order; do
operator-surface first so the init/doctor fix lands before the credential-mode
change.

## Step 01 — operator-surface (the *how*)

Main doc §2.1, §2.3, §2.4. Fail-closed and one-source-of-truth throughout.

* **Wizard** (`src/catraz/commands/setup/_wizard_interactive.py`,
  `_wizard_yes.py`, `_from.py`, `_secrets.py`):
  * Replace the `GITLAB_URL`/`GITLAB_MODE` prompts with one **host** prompt
    (default `gitlab.com`); stop writing `GITLAB_MODE`/`GITLAB_URL` to `.env` and
    drop them from `_from` inheritance.
  * Prompt read token always; write token when the user wants write access.
    Write host-keyed lines (`<host> <token>`) into `read_tokens`/`write_tokens`,
    upserting the host line. Add a grouped-token upsert helper in `_secrets.py`
    mirroring the warden's parse rule (`warden/core/config_load.py:_parse_token_file`).
  * Ensure a `[[git.endpoint]]` (`host`, `type="gitlab"`) in `warden.toml`. Add
    an "ensure endpoint" helper next to the existing `set_toml_list` /
    `remove_toml_key` / `_read_toml_allowed_projects` in `src/catraz/policy.py`.
  * Keep writing top-level `allowed_projects` / `branch_prefixes` (main doc §4/F1
    — that is what the runtime enforces).
* **Doctor** (`src/catraz/doctor.py`): delete `SECRETS`, `check_gitlab`,
  `_gitlab_mode`; `_doctor_fix` scaffolds only `read_tokens`/`write_tokens`
  (+`anthropic_api_key` under api_key). Drive `tokens`/`policy`/`endpoints` off
  configured endpoints + present tokens ("no endpoint configured" replaces the
  `GITLAB_MODE=off` branch). Keep the vocabulary mirror in lockstep with
  `guards/git/actions.py`.
* **Template + env**: fix `assets/config/warden.toml`'s header comment (drop the
  `.env` token-mode pointer; describe the endpoint model; note F1). Align the
  `.env.example` auth note with the two credential modes.
* **Tests + sweep**: update `tests/cli/test_secrets.py`, `test_init_wizard.py`,
  `test_init_from.py`, `test_doctor_gitlab.py`, `test_doctor_endpoints.py`,
  `test_doctor_actions.py`, `test_auth.py`. Add: wizard writes host-keyed grouped
  tokens + an endpoint; a generated `warden.toml`+token pair loads through the
  warden's `config_load` with `access_mode(host)` correct. Sweep `src/`,
  `tests/`, docs for `GITLAB_MODE`, `GITLAB_URL`, `gitlab_read_token`,
  `gitlab_write_token` — none may survive.

## Step 01 — credential-modes (the *how*)

Main doc §2.2.

* **Compose overlay**: add `src/catraz/assets/compose/home.persistent.yml`
  binding `${PROJECT_DIR}/.catraz/state/${AGENT_PROFILE:-claude}` →
  `/home/dev/.claude` (replacing the tmpfs mount for that path). Select it in
  `src/catraz/compose.py` the way `auth.<mode>.yml` is layered (`_build_layered`,
  ~line 134), resolving `credentials.mode` from the agent manifest
  (`catraz.agents.load_manifest` / `resolve_agent_profile`). `sync` keeps the
  tmpfs home + `.ro` seed.
* **Adapter** (`src/catraz/assets/agents/claude/adapter.py`): delete
  `_wire_persistent`; the persistent branch of `prepare_home` writes
  `.claude.json`/`settings.json` only when absent and merges onboarding/trust
  flags into an existing `.claude.json` instead of overwriting.
* **Entrypoint** (`src/catraz/assets/container/entrypoint.py`): drop the
  `/var/lib/agent-state` symlink assumptions for the credential; keep the
  `AGENT_LOG_DIR` debug-log bind.
* **Tests**: `tests/container/test_build_home.py`
  (`test_prepare_home_persistent_*` assert full-bind merge semantics, not
  symlinks); a test that a credential written into the bound home survives a
  simulated restart (new container object, same `state/<profile>`). Confirm the
  one-off `run`/`shell` path (`src/catraz/commands/run.py`) picks up the overlay.

## Verification (every step)

```
uv run --with pytest python -m pytest tests/cli tests/container -q
uv run mypy
```

Guard: `cd warden && uv run pytest -q` stays green (warden untouched).
End-to-end for step 01/operator-surface: `catraz init --yes` in a temp dir with a
host+token in env, then assert the token files are host-keyed, `warden.toml`
gained the endpoint, and the warden's `config_load` reports the host read-write.

## Hard rules

Identity **EddyXorb**, no AI/tool mentions anywhere. One commit per step, green
each time. Docstrings/comments per `docs/RULES.md`.
