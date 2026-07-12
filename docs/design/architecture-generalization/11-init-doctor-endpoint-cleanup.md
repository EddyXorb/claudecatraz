# 11 — Init, doctor, and credential modes on the endpoint model

The warden runtime, `docker-compose.yml`, and `.env.example` speak the endpoint
model in full (08-multi-target → 09-endpoint-actions → 10-actions-rework): every
routable host is a `[[git.endpoint]]`, tokens are grouped host-keyed
`read_tokens`/`write_tokens` secret files, and a host's access mode is derived
from which of its tokens are present — there is no global GitLab "mode" anymore.

The **operator-facing surface never followed**. `catraz init`, `catraz doctor`,
and the shipped `warden.toml` header still speak the pre-endpoint,
single-GitLab language. The result is not cosmetic: a fresh setup is broken
end to end. This document is the *what/why*; the step-by-step *how* lives in the
subfolder [`11-init-doctor-endpoint-cleanup/`](11-init-doctor-endpoint-cleanup/00-index.md)
and derives from here — on contradiction, this document wins.

> Prerequisite: 08, 09, 10 fully implemented. This is a pre-1.0 cleanup of the
> operator surface only; no compatibility shim for the old init inputs.

---

## 1. What is broken today

* **Tokens land in files nothing reads.** The wizard
  (`_wizard_interactive._prompt_gitlab_tokens`, `_wizard_yes._yes_apply_tokens`
  via `doctor.SECRETS`) writes `.catraz/secrets/gitlab_read_token` /
  `gitlab_write_token`. Compose mounts only `read_tokens` / `write_tokens`
  (grouped), which `doctor._doctor_fix` scaffolds empty. A user who pastes valid
  tokens during `init` gets a warden that runs **closed** for every git op.
* **No endpoint is ever created.** The wizard writes `allowed_projects` and
  `branch_prefixes` but never a `[[git.endpoint]]`; the template ships them all
  commented out. With no endpoint, no host is routed — even with correct tokens.
* **`GITLAB_MODE` / `GITLAB_URL` are vestigial.** The warden never receives them
  and derives mode from tokens. The wizard still prompts for them and writes them
  to `.env`; doctor gates checks on them. `.env.example` already declares them
  gone, so init contradicts the shipped docs; the `warden.toml` header comment
  still points at `.env` for token mode.
* **Doctor is half-migrated.** `check_tokens`, `check_action_coherence`, and
  `check_endpoints` already reason in the endpoint model, but `SECRETS`,
  `_doctor_fix`, `_gitlab_mode`, and `check_gitlab` still carry the old world.
* **Persistent credential mode persists nothing.** `agent.toml` sets
  `credentials.mode = "persistent"`, wired as a symlink
  `~/.claude/.credentials.json → /var/lib/agent-state/.credentials.json` inside a
  tmpfs home. Claude writes the credential atomically (temp file + rename);
  `rename()` replaces the symlink with a regular file in the tmpfs, so the bind
  stays empty and the login is lost on container exit. Every run re-prompts.

## 2. The target

### 2.1 Init produces a routable single-host setup

The common case — one GitLab, one token pair — must work with no manual TOML
editing. The wizard asks for the git **host** (default `gitlab.com`), a read
token, and optionally a write token, then:

* writes host-keyed lines (`<host> <token>`) into `read_tokens` / `write_tokens`,
  upserting the host's line;
* **offers** to add a `[[git.endpoint]]` (`host`, `type = "gitlab"`) to
  `warden.toml` — the shipped template ships no endpoint (a host runs only once
  the operator or this prompt adds one), so declining leaves a valid,
  endpoint-less config that doctor treats as "no host configured";
* keeps writing `allowed_projects` and `branch_prefixes` where the runtime
  actually enforces them (§4), and asks for the project allowlist as today.

"read-only vs read-write" is no longer a stored mode — it is exactly "did the
user give a write token", the same fact the warden derives in `access_mode`.
`GITLAB_MODE` / `GITLAB_URL` are removed from the wizard, `_from` inheritance,
and `.env`. Multi-host stays a hand-edit of `warden.toml` plus more token lines;
the wizard does not loop hosts.

### 2.2 Two credential modes, honestly implemented

`credentials.mode` in the agent manifest selects between two, and only two,
strategies — a compose overlay per mode, chosen the way auth overlays already
are (`compose._build_layered` layers `auth.<auth_mode>.yml`):

* **`sync`** — the live home is tmpfs; the host `~/.claude` credential is mounted
  read-only under `.ro/` and copied in at start. Maximum isolation, no
  write-back; token refresh is not persisted. Unchanged from today.
* **`persistent`** — the live home *is* the per-repo persistent store:
  `.catraz/state/<profile>/` bind-mounted at `/home/dev/.claude`. Atomic writes
  stay inside the bind, so login and refresh persist across runs. This replaces
  the selective-symlink wiring, which is deleted.

There is deliberately no middle option. A "persist only the credential, rebuild
settings/hooks fresh" variant cannot be done at file granularity (the atomic
rename defeats a per-file symlink), and a "full bind but re-stamp the executable
surfaces each start" variant buys little: a compromised session still has a whole
writable state dir to persist instructions in. `persistent` accepts that the
state dir is agent-writable and durable; the network isolation
(agent-net internal + forward proxy) remains the containment boundary. Choose
`sync` when that durability is not wanted.

In `persistent` mode `prepare_home` seeds `.claude.json` / `settings.json` only
when absent and merges the onboarding/trust flags into an existing
`.claude.json` rather than overwriting it, so a persisted home is not clobbered
each start.

### 2.3 Doctor speaks only the endpoint model

* `_doctor_fix` scaffolds `read_tokens` / `write_tokens` (and
  `anthropic_api_key` under api_key auth) — never the old per-token files.
* The `tokens` / `policy` / `endpoints` sections key off configured endpoints and
  present tokens. "No endpoint configured" replaces the `GITLAB_MODE=off`
  short-circuit; `check_gitlab` / `_gitlab_mode` are removed.
* `check_agent`'s persistent branch validates `.catraz/state/<profile>/` as the
  real home (credential at `<state>/.credentials.json`), consistent with §2.2.
* The doctor keeps its own mirror of the action vocabulary and cascade; it stays
  in lockstep with `guards/git/actions.py` (verified by a table-driven test).

### 2.4 Template and `.env` tell one story

`assets/config/warden.toml` is slimmed to the keys plus short comments and a
pointer: the long action-vocabulary table and prose move to
`assets/config/README.md` (the operator reference), so the shipped toml is
scannable (~30 lines) rather than a wall of comments. The template keeps the
git-namespace defaults (`[git].actions`, `[git.rules]`) but ships no
`[[git.endpoint]]`. `.env.example` (already free of `GITLAB_MODE` /
`GITLAB_URL`) has its auth note aligned with the two credential modes.

### 2.5 The credential mode is operator-selectable

The choice between the two modes of §2.2 is a setup decision, so the operator
makes it at `init` rather than by hand-editing the shipped manifest. A
`.catraz/.env` key `CLAUDE_CREDENTIALS_MODE` (`persistent` | `sync`) overrides
the agent manifest's `credentials.mode`; absent, the manifest default
(`persistent`) stands. The wizard prompts for it (default `persistent`) and
writes the key; `doctor` reports the active mode and validates the value, but
does not prompt.

The mode drives two places that must agree: the host picks the compose overlay
(`compose._credentials_mode`) and the in-container adapter branches
`prepare_home` on it. The override therefore resolves from `.env` on the host
and is passed into the container as an env var (the way `AUTH_MODE` already is),
so both read the same effective mode. An unset or unknown value falls to the
manifest default; a value the operator set but mistyped is a `doctor` finding,
never a silent mode switch.

## 3. Behavioral changes vs. today

* A fresh `catraz init` for one GitLab host yields a warden that actually routes
  and authorizes that host — no manual `warden.toml` edit.
* `GITLAB_MODE` / `GITLAB_URL` no longer exist as inputs anywhere; the host comes
  from the endpoint, the mode from the tokens.
* `persistent` credential mode survives container exit; `sync` is unchanged.
* The old secret filenames `gitlab_read_token` / `gitlab_write_token` are gone;
  no shim reads them.

## 4. Known warden gaps this cleanup routes around (not fixed here)

These are runtime-model questions, surfaced so the operator surface targets what
the warden *actually enforces* rather than what the config appears to say:

* **F1 — per-endpoint `allowed_projects` is dead config.** The kernel authorizes
  projects only against the global top-level `allowed_projects`
  (`Config.project_allowed`, `transport.py`, the `__main__` startup warning).
  `GitEndpoint.allowed_projects` / `Config.git_project_allowed` are parsed and
  validated but never called. Per-host project isolation therefore does not
  exist; the template's per-endpoint `allowed_projects` examples do nothing. The
  wizard consequently writes the **top-level** `allowed_projects` (which is
  enforced). Fixing enforcement — making project scoping host-aware — is its own
  change with a security-widening review and is left for a later step.
* **F2 — legacy top-level rule keys.** `branch_prefixes` / `allowed_projects` /
  `max_*` at the top level coexist with `[git.rules]` / `[[git.endpoint]]` as a
  documented back-compat fallback. The template mixes both idioms; collapsing to
  one is a separate pre-1.0 tidy.

## 5. Conventions for the implementation

Author and committer are the repo identity **EddyXorb**; no co-authorship or
"generated with" trailers, no mention of AI tools anywhere (commits, branches,
descriptions). One commit per step, each leaving the full verification green
(`uv run --with pytest python -m pytest tests/cli tests/container -q`,
`uv run mypy`; the warden suite unaffected but run as a guard). Docstrings and
comments follow `docs/RULES.md`: short, no history, no obvious statements, no
markup, and no references to files under `docs/design/`.
