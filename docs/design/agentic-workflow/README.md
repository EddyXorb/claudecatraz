# Agentic Workflow — GitLab-Sandbox für Claude

Security-Architektur-Referenz. Stufen 01 und 02 sind implementiert.

---

## Status

| Stufe  | Plan                                                       | Inhalt                                                  | Status |
| ------ | ---------------------------------------------------------- | ------------------------------------------------------- | ------ |
| **01** | [`01-bootstrap-hardening.md`](./01-bootstrap-hardening.md) | Token-Leak geschlossen, dediziertes Claude-Konto        | ✅      |
| **01** | [`01-gitlab-native.md`](./01-gitlab-native.md)             | Service Account, Protected Branches, Push Rules, Tokens | ✅      |
| **02** | [`02-warden.md`](./02-warden.md)                           | Warden: API-Filter, git-G1-Proxy, State, Logging        | ✅      |
| **02** | [`02-forward-proxy.md`](./02-forward-proxy.md)             | Squid-Allowlist, Netz-Isolation                         | ✅      |
| **03** | [`03-observability.md`](./03-observability.md)             | Log-Viewer + optional Grafana/Loki                      | ⏳      |
| **03** | [`03-testing-redteam.md`](./03-testing-redteam.md)         | Red-Team-Suite, CI                                      | ⏳      |
| **04** | [`04-cli.md`](./04-cli.md)                                 | `claudecatraz`-CLI: ein Vordereingang fürs Setup        | ⏳      |
| **05** | [`05-repackaging.md`](./05-repackaging.md)                 | `catraz` als installierbares Werkzeug, `.catraz/`-Heim, Image-Schichtung, Auth-Modus | ⏳      |

> Stufe 05 wurde in drei adversarialen Review-Runden gehärtet — Kritik & Antworten in
> [`roastiteration-1.md`](./roastiteration-1.md) · [`roastiteration-2.md`](./roastiteration-2.md) ·
> [`roastiteration-3.md`](./roastiteration-3.md) (Sicherheits-Löcher → Einfachheit zurückgewinnen → Topologie-Entscheidung).

---

## Begriffe

- **Egress** — ausgehender Netzwerkverkehr eines Containers. `agent-net` ist `internal: true` → der Agent hat keine eigene Internet-Route.
- **Warden** — der Policy-Enforcement-Proxy zwischen Agent und GitLab. Einziger Halter der GitLab-Credentials, einzige Vertrauensgrenze.

---

## 1. Regeln (verbindlich)

| #      | Regel                                                                                        |
| ------ | -------------------------------------------------------------------------------------------- |
| **R1** | Lesen erlaubt auf alles (MRs, CI, Repos, Pipelines, ...).                                    |
| **R2** | Schreiben (push) nur auf Branches mit konfigurierbarem Präfix (default `claude/`).           |
| **R3** | MRs erstellen, kommentieren, CI triggern — nur für eigene (`claude/*`)-Branches.             |
| **R4** | MRs dürfen von Claude **niemals gemerged** werden.                                           |
| **R5** | Obergrenzen: max. offene MRs, max. Branches, max. schreibende Aktionen/Stunde.               |
| **R6** | Alle Regeln architektonisch erzwungen — nicht per Tool-Config, die der Agent umgehen könnte. |

```
BRANCH_PREFIX       = "claude/"
MAX_OPEN_MRS        = 5
MAX_OPEN_BRANCHES   = 10
MAX_WRITES_PER_HOUR = 60
```

---

## 2. Bedrohungsmodell

**Annahme (Worst Case):** Der Agent ist vollständig kompromittiert. Er kann beliebige Prozesse starten, das Dateisystem lesen, Umgebungsvariablen auslesen und rohe HTTP-/git-Requests senden.

**Kernprinzip (R6):** Jedes Credential im Prozessraum des Agenten gilt als kompromittiert. Im Agent-Container liegt daher **kein** GitLab-Token; der einzige Netzweg zu GitLab führt durch den Warden.

**Was die Architektur leistet:** Die GitLab-Aktionen des Agenten bleiben policy-konform, unabhängig davon wie böswillig der Agent intern ist.

**Was sie bewusst nicht leistet:**
- **Code-Poisoning:** aufgefangen durch R4 + menschliches MR-Review (kein Self-Merge).
- **Exfiltration über R1:** begrenzt durch Egress-Allowlist + Audit-Log, nicht eliminiert.
- **Andere Secrets im Agent-Container:** per Axiom kompromittiert → nichts außer dem Notwendigen mounten.

### 2.1 Claude-Credentials (irreduzible Schwachstelle)

GitLab-Tokens lassen sich aus dem Agenten heraushalten (Warden). Die Claude-Credentials nicht — der Agent braucht sie. **Mitigation:** dediziertes Sandbox-Konto (nie das Primärkonto), Egress-Lock gegen Exfiltration, Revozierbarkeit.

---

## 3. Architektur

```
   Host (VSCode)                    ┌─────────────────────────────────────┐
   editiert workspace/ ◀─ bind ───▶│ docker network: agent-net            │
   (außerhalb Container)            │  (internal: true → KEIN Egress)     │
                                    │                                     │
   ┌──────────────────┐  git+REST  ┌────────────────────────┐             │
   │  claude-dev-env  │───────────▶│    gitlab-warden        │            │
   │  KEIN GitLab-    │            │  API-Filter + G1-Proxy  │─ egress ──▶ gitlab.com
   │  Token (R6)      │            │  Quoten, JSONL-Log      │            │
   └────────┬─────────┘            └────────────────────────┘             │
            │ http(s)_proxy        ┌────────────────────────┐             │
            └─────────────────────▶│  forward-proxy (Squid) │─ egress ──▶ npm/crates/...
                                   │  Domain-Allowlist       │            │
                                   └────────────────────────┘             │
                                    └─────────────────────────────────────┘
```

**Schreibpfad:** Echter `git push` über den G1 Smart-HTTP-Proxy (SHA-erhaltend). Der bind-gemountete `workspace/` wird host-seitig mitbearbeitet; Commit-API scheidet aus, weil sie serverseitig fremde SHAs erzeugt und den geteilten Clone divergieren lässt.

**MCP-Sidecar:** nicht verwendet. Claude kennt die GitLab-REST-API und nutzt `git`/`curl` direkt gegen den Warden.

Details → [`02-warden.md`](./02-warden.md), [`02-forward-proxy.md`](./02-forward-proxy.md).

---

## 4. GitLab-native Schicht (Layer 2)

Fängt den Worst Case auf, wenn der Warden versagt. Zero-Code (reine GitLab-Konfiguration).

| Regel | Native Durchsetzung                                           |
| ----- | ------------------------------------------------------------- |
| R1    | `read_api`/`read_repository`-Scope                            |
| R2    | Push Rules `^claude/` + Protected Branches                    |
| R3    | Developer-Rolle (Ownership-Präzision zusätzlich durch Warden) |
| R4    | Protected Branches „merge: No one" + Approval-Regeln          |
| R5    | Warden-primär (Plattform-Rate-Limits nur grob)                |
| R6    | Minimal-Scopes + Service-Account-Rolle                        |

Details → [`01-gitlab-native.md`](./01-gitlab-native.md).

---

## 5. Referenz-Deployment

### Konfigurationsarten

| Art                | Wohin                                        | Beispiele                                                      |
| ------------------ | -------------------------------------------- | -------------------------------------------------------------- |
| **Secrets**        | `.env` (gitignored)                          | `GITLAB_READ_TOKEN`, `GITLAB_WRITE_TOKEN`, `ANTHROPIC_API_KEY` |
| **Tunables**       | `config/` (read-only gemountet, versioniert) | Allowlist, Branch-Präfix, Limits, `squid.conf`                 |
| **Laufzeit-State** | `./state/`, `./logs/` (Bind-Mounts)          | SQLite-Quoten-State, Audit-Logs                                |

In `config/` liegt **nie ein Geheimnis**.

### On-Disk-Layout

```
<compose-dir>/
├── docker-compose.yml
├── .env                     # Secrets (gitignored)
├── config/                  # read-only gemountet, host-editierbar (KEINE Secrets)
│   ├── allowlist.txt        #   Forward-Proxy: erlaubte Domains
│   ├── squid.conf           #   Forward-Proxy: Squid-Konfiguration
│   └── warden.toml          #   Warden: Präfix, Limits, erlaubte Projekte
├── workspace/               # Agenten-Working-Clone + host-seitig editiert (VSCode)
├── claude/                  # Claude-Home (nur Sandbox-Credential)
├── state/
│   └── warden/              #   SQLite-Quoten-State (state.db + WAL)
└── logs/
    ├── warden/              #   warden-audit.jsonl
    └── squid/               #   access.log (Egress-Audit)
```

### `.env`

```dotenv
ANTHROPIC_API_KEY=        # dediziertes Sandbox-Konto — niemals das Primärkonto

# NUR der Warden bekommt diese (nie der Agent, R6):
GITLAB_READ_TOKEN=        # read_api, read_repository
GITLAB_WRITE_TOKEN=       # api (Service-Account/Developer)

CLAUDE_HOME=./claude
PROJECT_DIR=./workspace
```

### `docker-compose.yml`

```yaml
services:
  claude-dev-env:
    build:
      context: .
      dockerfile: Dockerfile
      args:
        DEV_UID: ${DEV_UID:-1000}
    networks: [agent-net]                        # NUR agent-net → keine Internet-/GitLab-Route
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - GITLAB_API_URL=http://gitlab-warden:8080/api/v4
      - http_proxy=http://forward-proxy:3128
      - https_proxy=http://forward-proxy:3128
      - no_proxy=gitlab-warden
      # KEIN GITLAB_API_TOKEN / GITLAB_GIT_TOKEN (R6)
    volumes:
      - ${CLAUDE_HOME:-./claude}:/home/dev/.claude
      - ${PROJECT_DIR:-./workspace}:/workspace
    working_dir: /workspace
    tty: true
    stdin_open: true
    restart: unless-stopped
    depends_on:
      gitlab-warden:  { condition: service_healthy }
      forward-proxy:  { condition: service_healthy }

  gitlab-warden:
    build: ./warden
    networks: [agent-net, egress-net, admin-net]
    environment:
      - GITLAB_READ_TOKEN=${GITLAB_READ_TOKEN}
      - GITLAB_WRITE_TOKEN=${GITLAB_WRITE_TOKEN}
    volumes:
      - ./config/warden.toml:/etc/warden/warden.toml:ro
      - ./state/warden:/var/lib/warden
      - ./logs/warden:/var/log/warden
    read_only: true
    tmpfs: [/tmp]
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9090/healthz')"]
      interval: 5s
      timeout: 3s
      retries: 10
    restart: unless-stopped

  forward-proxy:
    build: ./forward-proxy
    networks: [agent-net, egress-net]
    volumes:
      - ./config/squid.conf:/etc/squid/squid.conf:ro
      - ./config/allowlist.txt:/etc/squid/allowlist.txt:ro
      - ./logs/squid:/var/log/squid
    read_only: true
    tmpfs: [/var/spool/squid, /tmp]
    healthcheck:
      test: ["CMD", "squidclient", "-h", "127.0.0.1", "mgr:info"]
      interval: 5s
      timeout: 3s
      retries: 10
    restart: unless-stopped

networks:
  agent-net:  { internal: true }   # KEIN Egress — Agent erreicht nur Warden + Proxy
  egress-net: {}                   # Internet — nur Warden & Proxy
  admin-net:  {}                   # Healthz/Log-Viewer (Port 9090) — kein Agent
```

### Verzeichnisse anlegen

```bash
mkdir -p config state/warden logs/warden logs/squid workspace claude
chown -R "${DEV_UID:-1000}" state logs
```

### Quick-Ops

```bash
# Audit-Log verfolgen:
tail -f logs/warden/warden-audit.jsonl

# Egress prüfen:
grep <ziel> logs/squid/access.log

# Quoten-State inspizieren:
sqlite3 state/warden/state.db

# Allowlist neu laden (ohne Neustart):
docker compose exec forward-proxy squid -k reconfigure
```
