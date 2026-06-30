# Claudecatraz

**An autonomous Claude Code agent that may work on GitLab — under hard, non-bypassable rules.**

*No agent escapes the Warden.*

[![cli CI](https://github.com/EddyXorb/claudecatraz/actions/workflows/cli-ci.yml/badge.svg)](https://github.com/EddyXorb/claudecatraz/actions/workflows/cli-ci.yml)
[![warden CI](https://github.com/EddyXorb/claudecatraz/actions/workflows/warden-ci.yml/badge.svg)](https://github.com/EddyXorb/claudecatraz/actions/workflows/warden-ci.yml)
[![squid CI](https://github.com/EddyXorb/claudecatraz/actions/workflows/squid-ci.yml/badge.svg)](https://github.com/EddyXorb/claudecatraz/actions/workflows/squid-ci.yml)
[![redteam CI](https://github.com/EddyXorb/claudecatraz/actions/workflows/redteam-ci.yml/badge.svg)](https://github.com/EddyXorb/claudecatraz/actions/workflows/redteam-ci.yml)
[![compose validate](https://github.com/EddyXorb/claudecatraz/actions/workflows/compose-validate.yml/badge.svg)](https://github.com/EddyXorb/claudecatraz/actions/workflows/compose-validate.yml)

---

## What it does

A dockerized, hardened environment in which an **autonomous Claude Code agent** (with a toolchain of your choice, as long as it can be dockered) can work on GitLab projects — interactively (`catraz run`) or driven over Remote Control from claude.ai (`catraz run claude-remote`).

The point is the security model: the agent is treated as **potentially malicious**. It therefore holds **no GitLab credential whatsoever** and has **no internet route of its own**. Two purpose-built proxies sit in front of it instead:

- **Warden** — the sole holder of the GitLab tokens. It enforces rules R0–R6 on *every* git push and API call (only your own prefixed branches, no merge, quotas, …) and audits everything.
- **Forward proxy (Squid)** — the only way to the internet, filtered against a domain allowlist (npm, PyPI, crates, docs …). Default-deny, no TLS interception.

Even a fully compromised agent stays within policy: it cannot write to foreign branches, cannot merge anything, and can only talk to allowlisted destinations.

## Security model (R0–R6)

The agent is treated as **potentially malicious**. Six non-bypassable rules (R0–R6) are enforced
by the Warden on every git push and API call:

| #  | Rule | Enforced by |
| -- | ---- | ----------- |
| R0 | Mode gate — `GITLAB_MODE=off` denies all GitLab ops; `read-only` denies all writes | Warden checks the mode before any other rule and never sends a token upstream when the op is disabled |
| R1 | Read anything in the work scope | Read token in the Warden, GET pass-through |
| R2 | Push only to branches under the configured `branch_prefix` (default `claude/`) | Warden parses the git ref commands + GitLab push rules |
| R3 | MR/comment/CI only for your own branches | Warden API filter (ownership) + Developer role |
| R4 | **Never merge** | Warden blocks merge endpoints (403) + protected branches |
| R5 | Quotas (open MRs/branches, writes/h) | Warden state (SQLite, durable, fail-safe) |
| R6 | No token in the agent, network isolation | `agent-net internal` + Warden as the sole trust boundary |

> **The push prefix is configurable** — `claude/` is only the default. Set `branch_prefix` in
> `.catraz/config/warden.toml` (or `WARDEN_BRANCH_PREFIX`) to any prefix you like; R2 then
> enforces *that* one. It must be non-empty, so pushes are always confined to a namespace you
> choose, never to arbitrary or protected branches.

Two layers: the **Warden** (primary, code) and **GitLab-native** restrictions (backstop,
zero-code). If the Warden goes down, the agent structurally has **no** route to gitlab.com
(fail-closed). Details: [threat model & design](docs/design/agentic-workflow/README.md).

### Allowed write endpoints

REST **reads** (GET) pass through with the read token. REST **writes** are an explicit
allowlist — anything not listed is default-denied (`warden/warden/api_endpoints.py`):

| Method & path | What it does | Guard |
| ------------- | ------------ | ----- |
| `POST …/merge_requests` | open a merge request | source branch under `branch_prefix` (R2/R3) |
| `POST …/merge_requests/{iid}/notes` | post a top-level MR comment | MR authored by the service account (R3) |
| `POST …/merge_requests/{iid}/discussions` | start a discussion thread — incl. an **inline diff comment** on a file/line (pass a `position`) | MR authored by the service account (R3) |
| `POST …/merge_requests/{iid}/discussions/{discussion_id}/notes` | **reply** to an existing discussion thread | MR authored by the service account (R3) |
| `PUT …/merge_requests/{iid}` | edit the MR (title/description/labels, or close) | own MR (R3); `state_event=merge` blocked (R4) |
| `POST …/pipeline` | trigger a CI pipeline | ref under `branch_prefix` (R3) |
| `PUT …/merge_requests/{iid}/merge` | merge — **always 403** | never permitted (R4) |

> Inline review comments (`discussions`) need the MR's `position` (`base_sha`/`head_sha`/
> `start_sha` from the MR's `diff_refs`, plus `new_path` + `new_line`). The Warden only
> checks ownership and forwards the body untouched.

## Architecture

```text
   🧑‍💻 Host — VSCode / browser ───────────────┐  (read-only · admin unix socket)
        │                                       ▼
        │ Remote Control · claude.ai       🔍 Audit viewer · catraz audit --web
        │                                        │  (read-only)
 ┌──────┼────────────────────────────────────────┼──────────────────────┐
 │ agent-net · internal — NO egress              │                      │
 │      ▼                                         ▼                     │
 │  ┌──────────────────────┐   git + REST   ┌──────────────────────┐    │
 │  │ claude-dev-env       │   (no token)   │ gitlab-warden        │    │ ══▶ gitlab.com
 │  │ Claude Code          │ ─────────────▶ │ enforces R0–R6       │    │     (egress)
 │  │ no GitLab token (R6) │                │ holds all tokens     │    │
 │  │ [untrusted]          │                │ [trust boundary]     │    │
 │  └──────────────────────┘                └──────────────────────┘    │
 │      │                                                               │
 │      │ http / https proxy             ┌───────────────────────┐      │
 │      └───────────────────────────────▶│ forward-proxy         │      │ ══▶ npm · pypi ·
 │                                       │ Squid domain allowlist│      │     crates · docs
 │                                       └───────────────────────┘      │     (egress)
 └──────────────────────────────────────────────────────────────────────┘
```

Trust boundary: **gitlab-warden** is the sole holder of the tokens; the **claude-dev-env** agent
is untrusted and has no egress of its own — git/REST goes through the Warden, everything else
through the Squid forward proxy. The audit viewer reaches the Warden read-only over the admin
unix socket (host only).

Full design, threat model and rule set: **[`docs/design/agentic-workflow/`](docs/design/agentic-workflow/README.md)**.

## Install

`catraz` is a self-contained CLI (Python standard library only — Docker is the only real
dependency). Install it once and run it from **any** project directory afterwards:

```bash
git clone https://github.com/EddyXorb/claudecatraz
cd claudecatraz
uv tool install .          # installs the `catraz` command on your PATH
```

> Don't have [`uv`](https://docs.astral.sh/uv/)? `pipx install .` works too. Or skip the
> install entirely and run the repo-local shim `./catraz …` straight from the clone.

The packaged assets (compose file, Dockerfiles, default config) ship inside the tool and are
extracted to a per-version cache (`~/.cache/catraz/`) on first use — you never need the clone
again once installed.

**Prerequisites:** Docker Engine ≥ 24 + Compose ≥ 2.20, a **dedicated** Claude sandbox account
(not your primary one), and for GitLab a service account with two tokens (see
[GitLab-native setup](docs/design/agentic-workflow/01-gitlab-native.md)).

## Quick start

`catraz` sandboxes the **current** folder — like `git`, it works on the directory you run it
in. One interactive session sets everything up; a preflight (`doctor`) catches the silent traps
before they bite.

```bash
cd ~/code/my-project
catraz init           # wizard: create .catraz/, 3 secrets, allowed projects, credential sync
catraz run -p "hi"    # lazy-starts infra (Warden + Squid) and runs Claude one-off
```

> The always-on Remote-Control **daemon** is started with `catraz run claude-remote`
> (see *Interactive mode* below).

`catraz init` creates a single **`.catraz/`** directory in your project that holds all settings
and runtime files (config, credentials, logs, state) — nothing else is added to your repo, and
`.catraz/` is auto-added to `.gitignore`. The agent sees your project at `/workspace` but
**not** `.catraz/` itself (a tmpfs shadow hides it).

The agent is then reachable over Remote Control on claude.ai. Watch every GitLab decision live
with `catraz audit --web`.

## Basic commands

| Command | What it does |
| ------- | ------------ |
| `catraz init` | Wizard: create `.catraz/`, collect secrets + allowed projects (offers detected GitLab remotes), sync credentials |
| `catraz run …` | Run Claude Code **one-off** inside the sandbox (drop-in `claude`); lazy-starts infra |
| `catraz doctor` | Re-run the preflight; `--fix` repairs dirs/ownership |
| `catraz status` | Health per service, URLs, quota snapshot |
| `catraz ps` | List active agent containers for this repo |
| `catraz reload` | Rebuild + restart Warden/Squid whose `.catraz` config changed; `--force` rebuilds all infra even if unchanged or stopped |
| `catraz logs` | Tail logs (`agent`\|`warden`\|`proxy`, or `--audit`) |
| `catraz audit --web` | Open the read-only GitLab decision viewer (ephemeral loopback port) |
| `catraz sync` | Re-import the host's Claude credentials into the sandbox |
| `catraz allow <path>…` | Append GitLab project(s) to the warden allowlist |
| `catraz stop` | Stop the stack (alias: `catraz down`) |

Run `catraz <command> --help` for the details of any command. Full CLI design:
[`docs/design/agentic-workflow/04-cli.md`](docs/design/agentic-workflow/04-cli.md).

## Interactive mode (`catraz run`)

Besides the Remote Control daemon you can run Claude Code **interactively** inside the same
sandbox — a drop-in replacement for the `claude` binary:

```bash
alias claude='catraz run'
catraz run -p "fix the failing test"        # one-shot; exit code is passed through
```

`catraz run` starts a fresh **one-off** container (`docker compose run --rm`) with the project
mounted at `/workspace`; Warden + Squid stay up as daemons, so the second call is fast. Each
invocation owns its own container and lifecycle — independent of any `catraz run claude-remote`
daemon, so nothing you run here can be killed by another session tearing its stack down.
Everything after `run` (or after the mode) is handed **verbatim** to `claude`, so its own flags
need **no `--` separator** — `catraz run -p "…"` and `catraz run --dangerously-skip-permissions`
just work. Outside a `.catraz` project it fails closed — it never falls back to a host `claude`.

`run` takes an optional **mode** as its first token — `run [claude|claude-remote|shell] [args …]`
(default `claude`):

- `catraz run` / `catraz run claude …` — the ephemeral interactive agent (above).
- `catraz run claude-remote` — start the always-on **Remote-Control daemon** (`--profile remote
  up -d`), wait for health, and print the URLs; reachable on claude.ai.
- `catraz run shell` — open a shell in a one-off sandbox container (`catraz run shell ls`
  runs a command). This replaces the old `catraz shell` subcommand.

Because the bare first token is the mode, the only time you still need `--` is to pass a literal
arg that collides with a mode name (`catraz run -- shell`) or a flag meant for `claude` rather
than `catraz` (`catraz run -- --help`).

A **non-interactive** `catraz run` (e.g. `-p "…"` or piped — anything without a TTY) tees its
output to a durable per-run transcript at `.catraz/logs/agent/<timestamp>.log` (the newest 50
are kept; older ones are pruned). Because the one-off always runs with `--build` and stderr is
merged into stdout, the file also contains `docker compose` build/orchestration noise — it is a
raw session record, not a clean agent-only log. Interactive TTY runs get no transcript.

> ### ⚠️ What the sandbox protects — and what it does **not**
>
> The sandbox protects your **network and git egress**: the Warden is the sole holder of the
> GitLab tokens and enforces R0–R6, and Squid restricts the agent to an allowlist. It does
> **NOT** protect your **files** — `/workspace` is bind-mounted **read-write**, so the agent
> can read and modify any file in the project. Only `.catraz/` is hidden (a tmpfs shadow).
> Run it on code you're willing to let the agent change.

## Auth / Subscription

Two mutually exclusive modes, selected by `AUTH_MODE` in `.catraz/.env`:

- **`subscription`** (default) — the host `~/.claude/.credentials.json` is imported **read-only**
  into the container via `catraz sync`. The host credential is **never overwritten by the
  agent**: it runs in an untrusted context and must not be able to modify the host's long-lived
  token. (Source path configurable via `CLAUDE_CREDENTIAL_SOURCE`.)
- **`api_key`** — set `ANTHROPIC_API_KEY` instead; no credential file is mounted.

> **Token-refresh persistence:** Claude may refresh the OAuth access token during a long
> session. Because the in-container Claude home is a tmpfs, refreshed tokens are **not written
> back** to the host. If auth breaks after a long pause (Anthropic rotated the refresh token),
> re-run `catraz sync` to re-import the current credential from your host `~/.claude`.

## Custom base image

The agent image is a thin **Claude layer** built `FROM` a swappable **base**. By default it
builds the bundled C++/Rust/Python toolchain, but you can point it anywhere via `.catraz/.env`:

```dotenv
# BASE_IMAGE=ghcr.io/you/base:tag           # ready-made image (no build)
# BASE_DOCKERFILE=./docker/Dockerfile.base  # your own Dockerfile (relative to the project)
# BASE_CONTEXT=.                             # build context (default: the Dockerfile's dir)
```

The Claude layer (Node.js + `@anthropic-ai/claude-code` + the entrypoint) is always applied on
top, so you keep the security contract regardless of the base.

## Configuration

Everything lives under **`.catraz/`** in your project, with one source of truth per setting —
no value lives in both at once:

| Where | Holds | Visibility |
| ----- | ----- | ---------- |
| **`.catraz/secrets/`** (gitignored, 0600) | **Secrets** — one file each: `gitlab_read_token`, `gitlab_write_token`, (`anthropic_api_key`) — mounted as compose secrets | host only, per-service |
| **`.catraz/.env`** (gitignored) | **Non-secret wiring** — `AUTH_MODE`, `GITLAB_URL`, `GITLAB_MODE`, base image, `DEV_UID`, `WARDEN_*` overrides | host only |
| **`.catraz/config/warden.toml`** | **Non-secret policy** — branch prefix, R5 limits, allowed projects | mounted read-only into the Warden |

```text
# .catraz/secrets/ — one secret per file (mode 0600), never in .env
gitlab_read_token       # scopes: read_api, read_repository  — only the Warden (R6)
gitlab_write_token      # scope: api (service account / Developer) + read its own user
# anthropic_api_key     # only for AUTH_MODE=api_key
```

```dotenv
# .catraz/.env — non-secret wiring (NO tokens here)
AUTH_MODE=subscription             # subscription (host login) | api_key
GITLAB_URL=https://gitlab.com      # GitLab endpoint (self-hosted: change it)
GITLAB_MODE=read-write             # off | read-only | read-write
DEV_UID=1000                       # `id -u` on the host so bind mounts get the right ownership
```

> ### ⚠️ Write token must be able to read its own user
>
> The Warden resolves **which user the write token is** via `GET /user` and uses that
> identity to enforce **R3** (you may only comment on / edit / close MRs the service
> account authored). A classic personal access token with the **`api`** scope already
> covers this. A **fine-grained** PAT does **not** — by default it lacks the
> **`User: Read`** permission, so `GET /user` returns `403`, the Warden never learns its
> service account, and **every comment and MR edit is denied R3** — even though MR
> *creation* and `git push` still work (they only check the configured `branch_prefix`).
>
> So for the write token, use **either**:
> - a **classic** PAT with the `api` scope, **or**
> - a **fine-grained** PAT with the repo/MR permissions **plus `User: Read`** (`read_user`).
>
> `catraz doctor` probes this and fails loudly (`WRITE token cannot read its own user
> (GET /user → 403)`) so you catch it before the agent runs into silent R3 denials.

```toml
# .catraz/config/warden.toml — non-secret policy (the source of truth)
branch_prefix       = "claude/"    # R2: only branches with this prefix are pushable
max_open_mrs        = 5            # R5
max_open_branches   = 10           # R5
max_writes_per_hour = 60           # R5
allowed_projects    = ["group/sub/project-a", "group/sub/project-b"]
```

> **Precedence — env overrides the file.** For each policy setting there is an optional
> `WARDEN_*` env var (`WARDEN_BRANCH_PREFIX`, `WARDEN_MAX_OPEN_MRS`,
> `WARDEN_MAX_OPEN_BRANCHES`, `WARDEN_MAX_WRITES_PER_HOUR`, `WARDEN_ALLOWED_PROJECTS`).
> Set one (non-empty) to **override** `warden.toml` for that single setting; leave it
> empty/unset to use the file. So a value is read from exactly one place at a time —
> the env var if present, otherwise the toml.

> **Applying changes.** The Warden and Squid read their config once at startup, so an edit
> to `warden.toml`, `squid.conf`, `allowlist.txt`, or `.env` is inert until the container is
> recreated. Run `catraz reload` — it detects which infra service's config changed and
> rebuilds + recreates only that one. Use `catraz reload --force` to rebuild **all** infra
> regardless of staleness (and even when the stack is stopped) — the escape hatch when the
> image is stale but no config file changed, e.g. after a Warden source fix.

> ### ⚠️ `allowed_projects` — no wildcards
>
> Each entry must be the **full path of a concrete project** (from the namespace
> root). **Not supported:**
>
> - ❌ **Wildcards / globs / regex** — `group/*`, `group/**`, `*-ci` match nothing.
> - ❌ **Partial / leaf names** — `opt-ci` alone does not match `group/sub/opt-ci` (left-anchored).
> - ❌ **Group prefixes** — `group/sub` would even block Warden startup
>   (reconcile treats every entry as a concrete project → fail-closed).
>
> This is deliberate: an explicit, enumerable allowlist is auditable and keeps the
> read/exfiltration surface small (least privilege, design §6.10).

## Audit log

The Warden records **every** GitLab decision (allow/deny with rule R0–R6, R4/R5 highlighted).
View it in the browser with a single command — no fixed IP or published port; it forwards an
**ephemeral host-only loopback port** to the Warden's admin **unix socket** in `.catraz/`, so
parallel sandboxes never collide and the agent has no route to it:

```bash
catraz audit --web        # opens the read-only viewer (host only)
```

On the command line:

```bash
catraz audit -f                                # follow GitLab decisions (JSONL)
catraz logs --audit                            # same, via the logs command
tail -f .catraz/logs/squid/access.log          # egress destinations
sqlite3 .catraz/state/warden/state.db          # quota state
```

## Project layout

| Path | Purpose |
| ---- | ------- |
| `catraz` · `src/catraz/` | The CLI (front door) — `cli`, `compose`, `paths`, `auth`, `image`, `doctor`, … |
| `src/catraz/assets/` | All Docker build inputs: `compose/`, `claude-layer/`, `bases/`, `config/`, `container/entrypoint.py`, `AGENT.md` |
| `warden/` | Policy proxy (Python/Starlette) — the trust boundary, holds all tokens |
| `forward-proxy/` | Squid image with SNI-peek allowlist egress |
| `tests/cli/` · `tests/container/` | CLI + entrypoint unit tests (no Docker) |
| `tests/redteam/` | End-to-end bypass attempts (red team, Docker) |
| `docs/design/agentic-workflow/` | Design, threat model, implementation plans |

A sandboxed project, by contrast, only ever gets a single `.catraz/` directory.

## Tests

```bash
uv run --with pytest python -m pytest tests/ -q     # CLI + container unit tests
cd warden && uv run pytest                          # Warden unit/integration tests
```

CI (see badges above) checks the CLI, the Warden, Squid, compose validity, the Dockerfiles and
the red-team trust-boundary primitives on every push.
