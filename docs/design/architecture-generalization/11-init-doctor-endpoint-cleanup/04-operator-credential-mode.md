# 04 — operator-selectable credential mode

Derives from [`../11-init-doctor-endpoint-cleanup.md`](../11-init-doctor-endpoint-cleanup.md)
§2.5. On contradiction the main doc wins — report it, do not guess. Depends on the
level-01 credential-modes step (the two modes must already exist).

## How

A `.catraz/.env` key `CLAUDE_CREDENTIALS_MODE` (`persistent` | `sync`) overrides
the manifest `credentials.mode`; absent → manifest default (`persistent`). The
effective mode must be identical on the host (overlay selection) and inside the
container (adapter `prepare_home`).

* **Host resolver** (`src/catraz/compose.py:_credentials_mode`): read
  `CLAUDE_CREDENTIALS_MODE` from `.catraz/.env` (via `load_env`) first; if it is
  `persistent`/`sync`, use it; otherwise the manifest default. The existing
  fail-closed-to-`sync` on an unresolvable manifest stays.
* **Container passthrough** (`src/catraz/assets/compose/docker-compose.yml`): add
  `CLAUDE_CREDENTIALS_MODE=${CLAUDE_CREDENTIALS_MODE:-}` to the `claude-dev-env`
  service `environment:`, beside `AUTH_MODE`.
* **Adapter** (`src/catraz/assets/agents/claude/adapter.py`): the in-container
  `credentials_mode` resolves `os.environ["CLAUDE_CREDENTIALS_MODE"]` (when a
  valid value) over the manifest value, so `prepare_home` branches on the same
  mode the host overlay picked.
* **Wizard** (`_wizard_interactive.py`, `_wizard_yes.py`): after the auth-mode
  prompt, choose `persistent` (default) vs `sync`; write `CLAUDE_CREDENTIALS_MODE`
  to `.env`. The `--yes` path reads it from env/inherited, default `persistent`.
* **Doctor** (`src/catraz/doctor.py`): resolve and report the active mode
  (env override > manifest) in `check_agent`; validate a set
  `CLAUDE_CREDENTIALS_MODE` is `persistent`/`sync` (the way `AUTH_MODE` is
  validated). `check_auth` reads the same effective mode: in `persistent` the
  login lives in the container, so a missing host `.credentials.json` is not a
  finding and `catraz sync` is never suggested; in `sync` the host credential is
  required (its absence stays a `bad`). Doctor reports, never prompts.
* **Sync gating** (`src/catraz/commands/setup/_sync.py:_credentials_mode`): route
  through the shared resolver too, so `catraz sync` / auto-sync / the init-time
  hint agree with the overlay and adapter — a `sync` override against a
  `persistent` manifest must actually import the host credential.
* **`.env.example`**: document `CLAUDE_CREDENTIALS_MODE` beside `AUTH_MODE`, but
  keep the key **commented**. A fresh `init` seeds `.env` from this file, and the
  wizard only prompts when the key is absent — an uncommented default would seed
  the value and silently swallow the question.

## Tests

* compose: overlay selection honours the `.env` override in both directions,
  independent of the manifest value.
* adapter: `prepare_home` honours the `CLAUDE_CREDENTIALS_MODE` env override.
* wizard: interactive and `--yes` write the key; default is `persistent`.
* doctor: reports the active mode; flags an invalid value.
