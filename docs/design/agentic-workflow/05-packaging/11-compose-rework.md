# 11 — Compose rework: one generated compose file + a `secrets/` SSOT

**Goal:** make the compose invocation **traceable and reproducible** and pull every plaintext secret
out of `.env` into a single, file-based source of truth. Two coupled changes:

1. **Uniform run target "generate-then-run":** every catraz command runs **exactly**
   `.catraz/compose.resolved.yml` — the *effective* (merged + interpolated) compose, rendered from
   the layered sources. What runs == what you can inspect == what you can re-run by hand
   (`docker compose -f .catraz/compose.resolved.yml ps|logs|up`). Today catraz instead hands a
   *layered* invocation (`-f base -f auth --env-file … --profile remote`) to docker and lets compose
   merge in-memory — correct, but there is no single artifact to debug.
2. **A `.catraz/secrets/` folder:** the GitLab tokens move from `.env` into one file each, mounted
   into the **warden** via compose `secrets:`; the Anthropic key (`api_key` mode) moves into one file
   mounted into the **agent**. Each service reads its secret from `/run/secrets/…`. This removes the
   `.env`↔compose duplication **and** keeps the secrets out of the generated compose file.

**Why together:** rendering the effective compose with `docker compose config` *interpolates*
`${…}` into literal values — so a naive generated file would inline `${GITLAB_*_TOKEN}` **and**
`${ANTHROPIC_API_KEY}` (verified: in `api_key` mode the key lands in `resolved.yml`). Moving secrets
to compose `secrets:` makes them **file references** that `config` does **not** interpolate (it emits
`file: …/secrets/<name>`, never the value). So the secrets move is what lets the generated file be
secret-free. Sequencing the secrets moves **before** the generate-then-run switch means the generated
file is never born with inlined secrets. As belt-and-suspenders, `resolved.yml` is also written
`0600`.

## Scope decision

**In scope:** the two changes above + slimming `.env` to only the values that must be set *once
before start* (build args + non-secret wiring).

**Out of scope (deliberately):**
- **Live token reload.** The warden reads its config **once at process start** (`__main__.py:31`
  `cfg = from_env()`, cached in `Context.cfg`; it even resolves the service-account user id once,
  `context.py:45`). Editing a token — in `.env` *or* in `secrets/` — takes effect only after the
  warden restarts. File-based secrets are the *prerequisite* for any future live-reload (a container
  env var is frozen at container-create and can never change under a running process; only a mounted
  **file** can), but the reload itself is a separate warden feature with its own PR. Not done here.
- **Hiding `secrets/` from the host user.** Analyzed in its own section below — not achievable
  against the owning user in the same UID domain, and not where the security win comes from.
- **Migration / backward-compat.** The project is **pre-release with no deployed setups**, so these
  commits change `.env`/compose formats freely and assume a fresh `catraz init`. No `.env`→`secrets/`
  migration, no upgrade paths, no compatibility shims — those would be dead weight at this stage.
- Every keep-fixed security invariant from doc 10 (agent-net `internal`, `no-new-privileges`, the
  tmpfs `0700` shadow, egress routing, `--profile remote`, the service-name map).

**Preconditions:** current `feat/packaging` after doc 10 (CLI under `src/catraz/commands/`,
entrypoint at `src/catraz/assets/container/entrypoint.py`, compose at
`src/catraz/assets/compose/docker-compose.yml`, `compose.base_cmd`/`compose.run` in
`src/catraz/compose.py`, auth fragments in `src/catraz/auth.py`).

**Conventions (inherit `00-overview.md` + doc 10):** stdlib-only for `catraz`; one commit per
"Commit 11.x" block; `uv run --with pytest python -m pytest tests/ -q` green (and warden tests green
after any warden change) before each commit; Conventional-Commits subject, **no trailers**; work on
`feat/packaging`, never `main`.

## Findings that shaped this design (verified live on compose v5.2.0)

- **`config` is the only merge tool**, and a `${…}`-preserving single file is **not runnable**:
  `--no-interpolate` glues the project-dir onto `${PROJECT_DIR}` (doubled paths); adding
  `--no-path-resolution` misclassifies short-form binds as `type: volume` → `up` fails
  *"refers to undefined volume"*. **Only the fully interpolated `config` output round-trips cleanly**
  (`type: bind` correct, re-`config` green) — which is exactly why secrets must be `secrets:` (the one
  thing that stays a reference under interpolation).
- **The profile gate survives.** Rendering with `--profile remote` keeps `profiles: [remote]` on the
  agent, so a plain `docker compose -f resolved.yml up` still skips the agent; `--profile remote`
  (or `run claude-dev-env`) brings it in. One file serves both modes.
- **`config` normalizes some fields** — verified harmless-but-visible: `tmpfs … mode: 0700` is emitted
  as `mode: 448` (`0o700 == 448`, runs identically). **Consequence:** never grep `resolved.yml` for
  `0700`, and the security invariant must assert the *numeric* `448` (see Commit 11.4 / S2).
- **`config` tolerates a missing secret file** (RC=0, only a `secret file … does not exist` warning),
  so `generate_resolved` is **not** trapped in permanent fallback and `compose-validate` stays green —
  but `up`/`run` then fail at mount time with an opaque docker error (see Commit 11.2 / C2).
- **Warden runs as `uid = DEV_UID`** (`useradd --uid ${DEV_UID} warden`, gosu). Non-swarm compose
  `secrets:` bind-mount the host file **preserving owner/permissions**, so a `0600` file owned by the
  host user (= DEV_UID) is readable by the warden inside the container and by nobody else. (Caveat: if
  a user hand-edits `DEV_UID` away from `id -u`, the numeric owner no longer matches and the secret
  becomes unreadable inside the container — another reason 11.5 marks `DEV_UID` auto-derived.)

---

## Commit 11.1 — warden: read tokens from a file (`*_FILE`)

**Problem:** `config.from_env` (`warden/warden/config.py:157-158`) reads `GITLAB_READ_TOKEN` /
`GITLAB_WRITE_TOKEN` only from the environment. For file-based secrets the warden must read
`/run/secrets/…`.

**Solution — a `*_FILE` indirection, env value as fallback.** In `config.py`:
```python
def _secret(env: Mapping[str, str], name: str) -> str:
    """Read a secret from <name>_FILE (compose secret / mounted file) if set, else <name>.
    File wins so the running stack reads /run/secrets/…; the bare env var stays the fallback
    for tests and bare `docker run`. Trailing newline (common in token files) is stripped."""
    path = env.get(f"{name}_FILE")
    if path:
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError as e:
            raise ConfigError(f"{name}_FILE={path!r} unreadable: {e}") from e
    return env.get(name, "")
```
Use it for both tokens. `_validate` is unchanged (it already errors on an empty token, which now also
covers an empty/whitespace-only file → preserves today's clear fail-closed `ConfigError`). No behavior
change when only the env var is set, so `serve_test.py` and the existing `tests/test_config.py` keep
passing untouched.

**Tests** `warden/tests/test_config.py`: (a) `GITLAB_READ_TOKEN_FILE` → tmp file `"glpat-x\n"` →
`read_token == "glpat-x"`; (b) `*_FILE` **and** the bare env var set → the **file** wins; (c) `*_FILE`
→ missing path → `ConfigError`; (d) `*_FILE` → empty file → `_validate` raises the existing
"required" error.

`commit: "feat(warden): read GitLab tokens from *_FILE (compose secret) with env fallback"`

## Commit 11.2 — `.catraz/secrets/` for the GitLab tokens; always-create + migration

**Problem:** tokens live in `.catraz/.env` (interpolated into compose `environment:`), so they are a
second copy of a secret and would be inlined into the generated file.

**Solution — file-per-secret + compose `secrets:` into the warden only.**

- **`docker-compose.yml`** — add a top-level `secrets:` block, reference it on the warden, drop the two
  token env vars, add `*_FILE` pointers (hardcoded container paths → **no** new `${VAR}`, gate
  unaffected):
  ```yaml
  secrets:
    gitlab_read_token:  { file: ${PROJECT_DIR}/.catraz/secrets/gitlab_read_token }
    gitlab_write_token: { file: ${PROJECT_DIR}/.catraz/secrets/gitlab_write_token }

  services:
    gitlab-warden:
      secrets: [gitlab_read_token, gitlab_write_token]
      environment:
        - GITLAB_READ_TOKEN_FILE=/run/secrets/gitlab_read_token
        - GITLAB_WRITE_TOKEN_FILE=/run/secrets/gitlab_write_token
        # (GITLAB_READ_TOKEN / GITLAB_WRITE_TOKEN env lines removed)
  ```
  `${PROJECT_DIR}` is already gate-known. Mount lands at `/run/secrets/<name>`; host `0600`/DEV_UID
  preserved → warden-readable, nobody else. Agent untouched → still holds no token (invariant
  `agent carries a GITLAB_*_TOKEN` still holds).

- **Always-create the secret files.** A missing `file:` source makes compose fail `up` with an
  **opaque** *"bind source path does not exist"* instead of the warden's clear `ConfigError`. So
  `commands/setup.py` `cmd_init` and `doctor --fix` **always** create `.catraz/secrets/` (`0700`) and
  **both** token files (`0600`, **empty if no value entered**). An unconfigured stack then fails with
  the warden's clear "GITLAB_READ_TOKEN is required", not a docker mount error.
- Changing a token later = re-run `catraz init` or edit `.catraz/secrets/<name>` directly (plain
  host-file writes). `.catraz/` is already gitignored (`.gitignore:32`); add an explicit
  `.catraz/secrets/` line for signal.

- **`SECRETS` shape change is a cross-module break — land atomically (N1).** `setup.py:13` imports
  `SECRETS` from `doctor` and `setup.py:70` iterates `(env_key, prompt)` to write **.env**;
  `doctor.check_tokens`/`_probe_gitlab_tokens` also consume it (`doctor.py:139,158-160`). This commit
  changes `SECRETS` to `(filename, prompt, description)`, and updates **all** consumers + their tests
  in the **same** commit, or it lands red. doctor checks existence + non-empty of
  `.catraz/secrets/<filename>` and `_probe_gitlab_tokens` reads file contents. (`GITLAB_URL` stays an
  `.env` value — non-secret wiring; see 11.5.)

**Tests** `tests/cli/test_secrets.py`: `cmd_init` (monkeypatched `getpass`) creates
`.catraz/secrets/{gitlab_read_token,gitlab_write_token}` mode `0600`, dir `0700`, **even when blank**;
doctor reports `bad` on empty file, `ok` on non-empty; `doctor --fix` creates the files.

`commit: "feat(secrets): GitLab tokens in .catraz/secrets, mounted as compose secrets to warden"`

## Commit 11.3 — Anthropic key (`api_key` mode) → agent compose secret

**Problem (C1/N3):** in `api_key` mode `auth.write_auth_fragment` injects
`environment: { ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY} }` on the **agent** (not the warden — the
warden never reads it). `docker compose config` interpolates that → the key lands verbatim in
`resolved.yml`. The plan's "secret-free generated file" is false until this is fixed.

**Solution — mount the key as an agent secret; the entrypoint exports it.**
- **`auth.py` `API_KEY_FRAGMENT`**: replace the env injection with a compose secret on `claude-dev-env`
  (`secrets: [anthropic_api_key]`, top-level `anthropic_api_key: { file: …/secrets/anthropic_api_key }`)
  and `environment: [ANTHROPIC_API_KEY_FILE=/run/secrets/anthropic_api_key]`. The subscription fragment
  is unchanged.
- **`entrypoint.py` `cmd_start`**: before exec, resolve the key from `ANTHROPIC_API_KEY_FILE` (read +
  strip) falling back to `ANTHROPIC_API_KEY`, and set `os.environ["ANTHROPIC_API_KEY"]` so the `claude`
  process still sees it (claude needs it in env; the win is it is **not** in the compose service
  definition / `docker inspect` / `resolved.yml`). The `api_key`-but-no-key guard stays, now checking
  the resolved value.
- **`cmd_init`/`doctor`**: in `api_key` mode, `anthropic_api_key` is a managed secret file alongside
  the GitLab ones (created `0600`, empty allowed). The agent-carries-no-GitLab-token invariant is
  unaffected (the Anthropic key legitimately belongs to the agent).

**Tests** `tests/container/test_rc_args.py` extension: `ANTHROPIC_API_KEY_FILE` → entrypoint exports
the stripped value into `os.environ` before exec; render-leak guard in `tests/cli/test_compose_resolved.py`
(see 11.4) asserts the key value is absent from `resolved.yml`.

`commit: "feat(secrets): Anthropic api_key as an agent compose secret (not env-inlined)"`

## Commit 11.4 — uniform generate-then-run with an explicit (non-global) seam

**Problem:** there is no single compose artifact to debug; the run path is a layered multi-`-f` + env
invocation the user cannot reproduce with plain `docker compose`.

**Solution — render the effective compose and run that file; thread the decision explicitly.** In
`compose.py`:

- **Rename** today's `base_cmd` → `_source_cmd(root)` (the layered `-f base [-f auth] [-f override]
  --project-directory … --project-name … --env-file …`). Used **only** to generate.
- **`generate_resolved(root, extra_env=None) -> bool`:** run `[*_source_cmd(root), "--profile",
  "remote", "config"]` (interpolated; agent stays gated), capture stdout; on RC≠0 return `False`; else
  write `root/".catraz/compose.resolved.yml"` with a `#` header **at mode `0600`** (`os.open(...,
  0o600)` / `Path.write_text` then `chmod 0o600` — never the `0644` default). Header: *auto-generated,
  overwritten by state-changing commands; debug with `docker compose -f .catraz/compose.resolved.yml
  …`; regenerated per command, **host- and catraz-version-specific, not a portable artifact** (it
  bakes `${CATRAZ_ASSETS}` → `~/.cache/catraz/<version>/…`); secrets are file references, not values.*
- **Explicit seam, no module global (S4).** `prepare()` returns the run-target prefix; handlers thread
  it. No hidden `_USE_LAYERED`, no ordering landmine, no test leakage:
  ```python
  def prepare(root, *, render: bool, extra_env=None) -> list[str]:
      """Return the compose command prefix every subsequent call in this handler must use.
      render=True  (up/run/shell/down): write auth fragment + (re)generate resolved.yml.
      render=False (status/logs):       reuse an existing resolved.yml; never write side effects.
      Falls back to the layered source (loud warn) when no usable resolved file is available."""
      if render:
          auth.write_auth_fragment(root)
          if generate_resolved(root, extra_env):
              return _resolved_cmd(root)
          _warn_fallback("config render failed — running layered; resolved.yml may be stale")
          return _source_cmd(root)
      if (root / ".catraz/compose.resolved.yml").exists():
          return _resolved_cmd(root)
      return _source_cmd(root)          # read-only before first up → layered, no side effects

  def _resolved_cmd(root):
      # keep --project-directory for parity (S3); --project-name == baked name:, so ps/down/
      # --remove-orphans all key on the same project.
      return ["docker","compose","-f",str(root/".catraz/compose.resolved.yml"),
              "--project-directory",str(root),"--project-name",project_name(root)]
  ```
- **`run()`/`compose_ps`/`assert_invariants` take the prefix** (the threaded list) instead of calling a
  global `base_cmd`. `assert_invariants` therefore validates **exactly the artifact that will run** —
  on the happy path the resolved file, on the fallback path the layered source (S1: it can never
  green-light a stale resolved.yml that isn't executing).
- **`BASE_IMAGE` only for agent commands (C3).** `cmd_run`/`cmd_shell`/`up --remote` pass
  `extra_env={"BASE_IMAGE": image.resolve_base(root)}`; `up` (infra-only)/`down`/`status`/`logs` do
  **not** — otherwise a cold machine would `docker build` the multi-minute base just to render a
  `status` or tear the stack down. The agent service defaults `BASE_IMAGE: ${BASE_IMAGE:-}`
  (compose:92) and is profile-gated, so the resolved file is valid without it for non-agent paths.
- **Handlers**: each calls `prefix = compose.prepare(root, render=…, extra_env=…)` **first**, then
  passes `prefix` to `assert_invariants` / `run` / `compose_ps`. `--profile remote` is appended only
  for agent operations, exactly as today. Read-only `status`/`logs` use `render=False` → no auth-fragment
  write, no `config` fork (S5: status/logs stay cheap and side-effect-free).

**Why this still satisfies "uniform" (the chosen design):** every command's **run target** is the
single `_resolved_cmd` seam; uniformity is in the seam, not in forcing a re-render. State-changing
commands regenerate; read-only commands reuse the artifact the last `up` produced. Internal polling
(`compose_ps` in the health-wait loop) reuses the threaded prefix — it never re-renders.

**Also in this commit — tighten the tmpfs invariant (S2).** `assert_invariants` (`compose.py:137`)
only checks the `/workspace/.catraz` tmpfs `type`+`target`. Since `config`-normalization is now the
load-bearing path, add `mode == 448` (the normalized `0700`) and the `size` to the assertion, so a
future serializer that mangles the shadow's mode fails the security check instead of silently
weakening it. No acceptance text greps for `0700`.

**Tests** `tests/cli/test_compose_resolved.py` (no Docker — monkeypatch `compose.subprocess.run`):
(a) success → file written, **mode `0600`**, header present, `prepare(render=True)` returns the
resolved prefix incl. `--project-directory` + `--project-name`; (b) render RC≠0 → returns the layered
`_source_cmd` prefix + a warning; (c) `render=False` with an existing file → resolved prefix and **no**
`write_auth_fragment` call (assert via monkeypatch); (d) `render=False`, no file → layered, no side
effects; (e) `_source_cmd` equals today's layered prefix incl. `-f auth` when the fragment exists
(regression guard); (f) **leak guard**: a fake `config` stdout containing `sk-…`/`glpat-…` still ends
up in a `0600` file, and a positive test that with real secrets-as-files the prefix is correct.
`test_invariants.py`: tmpfs invariant requires `mode==448` and `size`. Existing
`tests/cli/test_run.py`/`test_shell.py` adapt to the `prepare`-returns-prefix handler shape.

`commit: "feat(compose): generate .catraz/compose.resolved.yml (0600) and run it; layered fallback"`

## Commit 11.5 — slim and re-categorize `.env` / `.env.example`

**Problem:** `.env` mixes four unrelated kinds of value; the `WARDEN_*` block duplicates `warden.toml`
(its real SSOT).

**Solution — `.env` keeps only what must be set once before start; clear section headers:**
- **Build** (consumed by `docker build`): `DEV_UID`, `BASE_IMAGE`/`BASE_DOCKERFILE`/`BASE_CONTEXT`,
  `NODE_VERSION`, `CLAUDE_CODE_VERSION`.
- **Wiring** (non-secret): `AUTH_MODE`, `CLAUDE_CREDENTIAL_SOURCE`, `GITLAB_URL`.
- **Removed:** `GITLAB_READ_TOKEN`/`GITLAB_WRITE_TOKEN`/`ANTHROPIC_API_KEY` (→ `secrets/`), the whole
  `WARDEN_*` policy block (SSOT is `warden.toml`; one comment line points there instead of five dead
  vars).
- **`DEV_UID`** comment: **auto-derived** from `id -u` by `catraz init` (`setup.py:62`) to line up
  bind-mount ownership *and* compose-secret readability (see Findings); **do not hand-edit** — a
  mismatch makes `/workspace`, the warden audit logs, **and the mounted secrets** unreadable.

**`.env.example`** mirrors the layout; `env-coverage` still passes (we only *deleted* token vars; the
gate checks compose→example, and the `secrets:` `file:` paths use already-known `${PROJECT_DIR}`).
Re-run the gate locally to confirm.

`commit: "refactor(env): slim .catraz/.env to build+wiring; secrets and policy move out"`

---

## Can `secrets/` be hidden from the host user?

Short answer: **not against the owning user, and that is not where the security comes from.** catraz
runs **as the host user** (same UID). A process cannot have access its invoking user lacks. True
invisibility needs a *different* privilege boundary, and each option breaks "catraz must be able to
change the token" or adds heavy machinery:

| Approach | Hides from owner? | catraz can change it? | Cost |
| --- | --- | --- | --- |
| `0600` plaintext in `.catraz/secrets/` (**this plan**) | No (owner) — yes vs *other* users, git, casual `cat` | Yes (plain file write) | none |
| root-owned `0600` + setuid helper | Only via a setuid root binary | Only via that helper (needs root) | a setuid root surface — worse than the problem |
| tmpfs-only runtime secret | Reduces *at-rest* exposure | Source must still persist somewhere → next row | needs a persistent source |
| OS keyring / `sops` / `pass` (encrypted at rest) | Encrypted at rest; owner can still unlock their own keyring | Yes (catraz writes the keyring) | a backend + dependency |

**What the `secrets/` folder actually buys us:** de-duplication (one SSOT, not `.env` + interpolated
compose), **secrets never inlined into the generated compose**, and **secrets are files not container
env vars** (an env var shows in `docker inspect` and is frozen at create; a `0600` file is neither).
That is the real win — not hiding a secret from its owner.

**Recommendation:** ship the `0600` file (dir `0700`) now; treat an encrypted-at-rest backend
(`CATRAZ_SECRETS_BACKEND=keyring|file`, catraz materializes into a **tmpfs** `secrets/` at `up`) as a
clean future opt-in. Do **not** advertise the plaintext file as hiding anything from the owner.

---

## Acceptance (Doc 11)
- `uv run --with pytest python -m pytest tests/ -q` green; warden tests green; compose CI
  (`compose-validate` incl. `env-coverage`, `squid-ci`, `redteam`) green.
- After `catraz up`, `.catraz/compose.resolved.yml` exists at **mode `0600`** and `docker compose -f
  .catraz/compose.resolved.yml ps` / `logs <svc>` work standalone (no `--env-file`, no extra env).
- **No secret value** appears in the generated file: a diff of every `.env`/`secrets/` value against
  `resolved.yml` is empty (not just `grep glpat` — also `sk-…`/the Anthropic key in `api_key` mode).
- Plain `docker compose -f .catraz/compose.resolved.yml config --services` lists only warden+proxy;
  with `--profile remote` it also lists the agent (gate survives).
- Secrets live only in `.catraz/secrets/*` (`0600`, dir `0700`), are **always created** (empty
  allowed); an unconfigured stack fails with the **warden's clear `ConfigError`**, not a docker mount
  error. The agent still carries no GitLab token (`assert_invariants` green); the Anthropic key
  (api_key) is a file, not service env.
- `catraz status`/`down`/`logs` never trigger `image.resolve_base` (no base build) and `status`/`logs`
  write no auth fragment and run no `config` fork (reuse the existing resolved file).
- A failed `config` render falls back to the layered run with a loud warning; `assert_invariants`
  validates the **same** artifact that runs in both the happy and fallback paths.
- tmpfs `/workspace/.catraz` invariant asserts `mode==448` and `size`; `.catraz/.env` no longer holds
  secrets or `WARDEN_*`; `DEV_UID` documented as auto-derived; `env-coverage` passes.
- Token reload still requires a warden restart — documented, by design (out of scope).
- All commits authored as the repo owner, no trailers.

---

## Changelog — roast iteration 1 incorporated
- **C1/N3:** Anthropic key was inlined into `resolved.yml` in `api_key` mode → new **Commit 11.3**
  makes it an agent compose secret; `resolved.yml` written **`0600`**; acceptance grep generalized.
- **C2:** missing secret file → opaque mount error → 11.2 **always-creates** the files (empty
  allowed).
- **C3:** `resolve_base` on every command built the base on cold machines → 11.4 resolves `BASE_IMAGE`
  **only for agent commands**.
- **S1:** `assert_invariants` now uses the threaded prefix, validating the artifact that actually runs
  (incl. the fallback path).
- **S2:** tmpfs invariant tightened to `mode==448`+`size`; no `0700` greps.
- **S3:** run target keeps `--project-directory`.
- **S4:** module-global `_USE_LAYERED` replaced by an explicit `prepare()`-returns-prefix seam.
- **S5:** read-only `status`/`logs` (`render=False`) skip the auth-fragment write and the `config`
  fork.
- **N1:** flagged the `SECRETS` shared-symbol break (setup+doctor+tests) to land atomically in 11.2.
- **N2:** header + acceptance document `resolved.yml` as host/version-specific, non-portable.
- **N4:** `DEV_UID` caveat extended to compose-secret readability.
