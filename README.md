# Claudecatraz

**An autonomous Claude Code agent that works on GitLab — under hard, non-bypassable rules.**

*No agent escapes the Warden.*

[![cli CI](https://github.com/EddyXorb/claudecatraz/actions/workflows/cli-ci.yml/badge.svg)](https://github.com/EddyXorb/claudecatraz/actions/workflows/cli-ci.yml)
[![warden CI](https://github.com/EddyXorb/claudecatraz/actions/workflows/warden-ci.yml/badge.svg)](https://github.com/EddyXorb/claudecatraz/actions/workflows/warden-ci.yml)
[![redteam CI](https://github.com/EddyXorb/claudecatraz/actions/workflows/redteam-ci.yml/badge.svg)](https://github.com/EddyXorb/claudecatraz/actions/workflows/redteam-ci.yml)

---

## What it is

Let an autonomous Claude Code agent loose on your GitLab projects — without trusting it.

Claudecatraz runs the agent in a Docker sandbox that treats it as **potentially
malicious**: it holds **no GitLab token** and has **no internet route of its own**. Every
git push, every API call, and every outbound connection goes through a proxy that enforces
policy and audits the decision. Even a fully compromised agent stays in bounds — it cannot
touch foreign branches, cannot merge, and can only reach destinations you allow.

You get the upside of an autonomous agent working on real repositories, without handing it
your credentials or your network.

## Why use it

- **Run an agent you don't fully trust.** A compromised or misguided agent still can't leak
  your token, write to protected branches, merge, or phone home off-allowlist.
- **Grant exactly the actions you want.** Enable or disable individual actions per
  endpoint — allow opening merge requests and pushing branches on one host while denying
  comments or CI triggers on another. The agent's capability set is precisely what you
  grant, nothing more.
- **Bring your own environment.** The agent image is a thin Claude layer built `FROM` a
  swappable base — point it at a ready-made image or your own Dockerfile and you get a
  reproducible, sandboxed dev environment with exactly the toolchain (compilers, runtimes,
  CLIs) you need.
- **Full audit trail.** Every GitLab decision is logged; watch them live in the browser.
- **Works like `git`.** Point it at any project directory and go.
- **Drive it from claude.ai** over Remote Control, or run one-off from the terminal.

> **Defense in depth — bring least-privilege credentials.** The sandbox is one layer, not a
> licence to hand it powerful tokens. Feed it credentials that are already scoped as tightly
> as they can be: a read token limited to `read_api`/`read_repository`, a write token on a
> dedicated service account with only the permissions it needs. The Warden then narrows that
> further. If a layer ever fails, the blast radius is still only what the token itself could
> ever do.

## Quick start

`catraz` is a self-contained CLI (Python stdlib only; Docker is the one real dependency).

```bash
git clone https://github.com/EddyXorb/claudecatraz
cd claudecatraz
uv tool install .          # installs `catraz` on your PATH (pipx works too)
```

Then, in any project:

```bash
cd ~/code/my-project
catraz init           # wizard: create .catraz/, secrets, allowed projects, credential sync
catraz run -p "hi"    # lazy-starts the sandbox and runs Claude one-off
catraz audit --web    # watch every GitLab decision live, read-only
```

`catraz init` creates one **`.catraz/`** directory holding all config, secrets and state —
nothing else touches your repo, and it's auto-added to `.gitignore`. The agent sees your
project at `/workspace` but never `.catraz/` itself.

**Prerequisites:** Docker Engine ≥ 24 + Compose ≥ 2.20, a **dedicated** Claude sandbox
account (not your primary), and a GitLab service account with a read and a write token.

> ### ⚠️ What the sandbox protects — and what it does not
> It protects your **network and git egress**: the Warden holds the tokens and enforces
> policy, and Squid restricts the agent to a domain allowlist. It does **not** protect your
> **files** — `/workspace` is bind-mounted read-write, so the agent can change any file in
> the project. Run it on code you're willing to let the agent edit.

## How it works

Three containers on an internal network with **no egress of its own**:

```text
 ┌─────────────────────────────────────────────────────────────────────┐
 │  agent-net · internal — the agent has NO direct route out            │
 │                                                                       │
 │   ┌──────────────────┐   git + REST    ┌──────────────────┐          │
 │   │ agent            │   (no token)     │ Warden           │ ──▶ gitlab.com
 │   │ Claude Code      │ ───────────────▶ │ holds all tokens │          │
 │   │ no token         │                  │ enforces policy  │          │
 │   │ [untrusted]      │                  │ audits every op  │          │
 │   └──────────────────┘                  └──────────────────┘          │
 │        │                                                              │
 │        │ http/https              ┌──────────────────────┐            │
 │        └────────────────────────▶│ forward proxy (Squid)│ ──▶ npm · pypi ·
 │                                   │ domain allowlist     │     crates · docs
 │                                   └──────────────────────┘            │
 └─────────────────────────────────────────────────────────────────────┘
```

- **Agent** — **Claude Code for now** (the sandbox is agent-agnostic by design; support for
  other agents will follow), running the toolchain of your choice. Untrusted. No credentials,
  no direct network.
- **Warden** — the sole holder of the GitLab tokens and the trust boundary. Reads pass
  through with a least-privilege token; writes are confined to the agent's own branch
  namespace — it may push its own prefixed branches and act on a merge request only when
  that request's source branch is in that namespace; merges, tag pushes and branch deletes
  are never allowed; quotas fail safe; everything is bounded by a per-endpoint action and
  project allowlist. Every decision is audited.
- **Forward proxy (Squid)** — the agent's only way to the internet, filtered against a
  domain allowlist. Default-deny.

If the Warden goes down, the agent structurally has **no** route to GitLab (fail-closed).

**Extensible by design.** The Warden gates raw **git** and the **GitLab** REST API today;
the guard layer takes new endpoints — **GitHub next**, and in principle any service (other
forges, databases, …) — without touching the sandbox or the security kernel.

## Common commands

| Command | What it does |
| ------- | ------------ |
| `catraz init` | Wizard: create `.catraz/`, collect secrets + allowed projects, sync credentials |
| `catraz run …` | Run Claude Code one-off in the sandbox (drop-in `claude`); lazy-starts infra |
| `catraz run claude-remote` | Start the always-on Remote-Control daemon (reachable on claude.ai) |
| `catraz run shell` | Open a shell in a one-off sandbox container |
| `catraz audit --web` | Open the read-only GitLab decision viewer |
| `catraz status` / `ps` | Health, URLs, quota snapshot / list active agent containers |
| `catraz reload` | Rebuild + restart infra whose `.catraz` config changed (`--force` for all) |
| `catraz allow <path>…` | Append GitLab project(s) to the Warden allowlist |
| `catraz sync` | Re-import the host's Claude credentials into the sandbox |
| `catraz stop` | Stop the stack |

Run `catraz <command> --help` for details.

## Configuration

Everything lives under **`.catraz/`**, one source of truth per setting:

| Where | Holds |
| ----- | ----- |
| `.catraz/secrets/` (0600) | GitLab tokens (`gitlab_read_token`, `gitlab_write_token`), optional API key |
| `.catraz/.env` | Non-secret wiring — `AUTH_MODE`, `GITLAB_URL`, `GITLAB_MODE`, base image, `DEV_UID` |
| `.catraz/config/warden.toml` | Non-secret policy — branch prefixes, quotas, allowed projects |

```toml
# .catraz/config/warden.toml
branch_prefixes     = ["claude/"]   # pushes are confined to these prefixes
max_open_mrs        = 5
max_open_branches   = 10
max_writes_per_hour = 60
allowed_projects    = ["group/sub/project-a", "group/sub/project-b"]
```

The Warden and Squid read their config once at startup; run `catraz reload` after an edit.
`allowed_projects` entries are full, concrete project paths — no wildcards, no group
prefixes (an explicit, enumerable allowlist keeps the read surface small).

## Custom base image

The agent image is a thin Claude layer on a swappable base. Point it anywhere via
`.catraz/.env`:

```dotenv
# BASE_IMAGE=ghcr.io/you/base:tag           # ready-made image (no build)
# BASE_DOCKERFILE=./docker/Dockerfile.base  # your own Dockerfile
```

The Claude layer (Node.js + `@anthropic-ai/claude-code` + entrypoint) is always applied on
top, so the security contract holds regardless of the base.

## Auth

Two modes, selected by `AUTH_MODE` in `.catraz/.env`:

- **`subscription`** (default) — the host `~/.claude/.credentials.json` is imported
  read-only via `catraz sync`; the agent can never overwrite the host credential. Re-run
  `catraz sync` if auth breaks after a long pause.
- **`api_key`** — set `ANTHROPIC_API_KEY` instead; no credential file is mounted.

## Project layout

| Path | Purpose |
| ---- | ------- |
| `catraz` · `src/catraz/` | The CLI (front door) |
| `src/catraz/assets/` | Docker build inputs: compose, Claude layer, bases, config, entrypoint |
| `warden/` | Policy proxy (Python/Starlette) — the trust boundary, holds all tokens |
| `forward-proxy/` | Squid image with domain-allowlist egress |
| `tests/` · `warden/tests/` | CLI, container, red-team and Warden tests |
| `docs/design/` | Design, threat model, implementation plans |

## Tests

```bash
uv run --with pytest python -m pytest tests/ -q   # CLI + container unit tests
cd warden && uv run pytest                         # Warden unit/integration tests
```

Full design and threat model: **[`docs/design/`](docs/design/)**.
