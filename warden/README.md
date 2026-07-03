# Warden

Policy-enforcement proxy between the Claude agent and gitlab.com — the single
trust boundary that holds **all** GitLab credentials and enforces rules R1–R6.
Implements the design in
[`docs/design/agentic-workflow/02-warden.md`](../docs/design/agentic-workflow/02-warden.md)
(Option C / Start variant).

## What it does

- **Read pass-through (R1):** REST `GET` and git `upload-pack`/`info-refs` are
  streamed to gitlab.com with a read-only token.
- **git write path (G1, R2/R5):** a transparent Smart-HTTP proxy parses the
  `receive-pack` ref-command section (pkt-line), enforces the branch prefix /
  no-delete / quota / rate, then streams the **unchanged** body upstream —
  SHA-preserving, so the host's shared working clone stays coherent.
- **API write filter (R3/R4/R5):** a data-driven allowlist of write endpoints
  with ownership checks, an unconditional merge block, and quota/rate limits.
- **Durable, fail-safe state (R5):** SQLite (WAL + `synchronous=FULL`); empty or
  unreconcilable state is **locked** ("limit reached"), never "free".
- **Auditable JSONL logging:** one writer, append-only, tokens never logged.

Default-deny everywhere; every allow path is explicit.

## Layout

```
warden/
├── pyproject.toml          # uv-managed, pinned deps
├── Dockerfile              # python:3.12-slim, non-root, read-only rootfs
├── warden/
│   ├── app.py              # Starlette routing               ← W4
│   ├── __main__.py         # uvicorn bootstrap + reconcile   ← W16
│   ├── errors.py           # deny / git-reject responses     ← W13
│   ├── core/               # kernel: pipeline (run_guarded), Intent/Decision,
│   │                       #   rules, capabilities, audit, state, config (§03.2/03.3)
│   └── guards/
│       ├── git/            # git Smart-HTTP guard: pktline, ref policy, GitGuard ← W7
│       └── gitlab_api/     # GitLab REST guard: catalog, read table, ApiGuard,
│                           #   ownership/reconcile, upstream token injection ← W6
└── tests/                  # policy / pktline / quota / api / git / redteam
```

The policy cores (`guards/*/policy.py`, `guards/git/pktline.py`, `core/model.py`,
`core/capabilities.py`, the catalog) are transport-free and pure — the foundation of the
test strategy and auditability (W2). Full module tree →
[`docs/design/agentic-workflow/02-warden.md`](../docs/design/agentic-workflow/02-warden.md).

## Develop

```bash
uv sync --extra dev      # create .venv, install pinned deps
uv run pytest            # full suite (git e2e auto-skips without `git`)
uv run pytest -k policy  # just the pure policy units
uv run ruff check .      # lint + import order + required annotations (ANN)
uv run mypy              # strict type-check of the warden package
```

## Run

Configured entirely via **Warden** env (never the agent's), see `config.py` / W10:

```bash
GITLAB_READ_TOKEN=...  GITLAB_WRITE_TOKEN=...  \
ALLOWED_PROJECTS=group/proj-a,group/proj-b     \
uv run warden            # serves :8080 (agent) and :9090 (admin/healthz)
```

`ALLOWED_PROJECTS` empty, or a missing token, aborts startup (fail-closed).

## Container

```bash
docker build -t gitlab-warden ./warden
```

Wire it into `docker-compose.yml` per W12: `agent-net` (`internal: true`),
`egress-net`, `admin-net`; mount `warden-state` and `warden-logs` volumes;
`read_only: true`. Remove `GITLAB_API_TOKEN` / `GITLAB_GIT_TOKEN` from the agent
service (closes the R6 leak, W12).
