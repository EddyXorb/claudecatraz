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
│   ├── pktline.py          # pkt-line parser (pure)        ← W7.2
│   ├── model.py            # core policy data types (pure)
│   ├── policy.py           # decide(req, state, cfg) (pure) ← W5
│   ├── api_endpoints.py    # REST write-endpoint table (pure) ← W6.1
│   ├── config.py           # env → Config, hard validation ← W10
│   ├── state.py            # SQLite quota state             ← W8
│   ├── audit.py            # JSONL logger                   ← W11
│   ├── upstream.py         # httpx + token injection        ← W9.2
│   ├── context.py          # runtime ctx + reconcile        ← W6.2/W8.2
│   ├── api_proxy.py        # REST filter                    ← W6
│   ├── git_proxy.py        # git G1 Smart-HTTP proxy        ← W7
│   ├── app.py              # Starlette routing              ← W4
│   └── __main__.py         # uvicorn bootstrap + reconcile  ← W16
└── tests/                  # policy / pktline / quota / api / git / redteam
```

`pktline.py`, `model.py`, `policy.py`, `api_endpoints.py` are transport-free and pure — the
foundation of the test strategy and auditability (W2).

## Develop

```bash
uv sync --extra dev      # create .venv, install pinned deps
uv run pytest            # full suite (git e2e auto-skips without `git`)
uv run pytest -k policy  # just the pure policy units
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
