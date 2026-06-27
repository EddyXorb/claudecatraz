# Warden — Implementierungsreferenz

Policy-Enforcement-Proxy zwischen dem Claude-Agenten und gitlab.com. Einzige Vertrauensgrenze; hält alle GitLab-Credentials.

Implementiert. Dev-Workflow → [`warden/README.md`](../../warden/README.md). Protokoll-Details → [`warden/GIT_SERVER_REFERENCE.md`](../../warden/GIT_SERVER_REFERENCE.md).

---

## Module

```
warden/
├── Dockerfile                  # python:3.12-slim, non-root, read-only rootfs
├── pyproject.toml              # uv-managed, gepinnte Deps
├── warden/
│   ├── __main__.py             # uvicorn-Bootstrap, Reconcile beim Start
│   ├── app.py                  # Starlette-App, Routing API vs. git
│   ├── config.py               # Env → typisiertes Config-Objekt, harte Validierung
│   ├── model.py                # Policy-Datentypen (pure)
│   ├── policy.py               # decide(request, state, cfg) → Decision  ← pure
│   ├── api_endpoints.py        # datengetriebene Write-Endpoint-Tabelle  ← pure
│   ├── api_proxy.py            # REST-Reverse-Proxy (GET-Passthrough + Write-Filter)
│   ├── git_proxy.py            # G1: 4 Smart-HTTP-Routen, Stream-Handling
│   ├── pktline.py              # pkt-line-Parser (receive-pack-Kommandos)  ← pure
│   ├── state.py                # SQLite-Zugriff: Quoten, Rate, Reconcile
│   ├── context.py              # Runtime-Ctx + Reconcile-Logik
│   ├── upstream.py             # httpx-Client, Token-Injektion
│   ├── audit.py                # JSONL-Logger, ein Schreiber, Redaction
│   └── errors.py               # Deny-/git-Fehler-Antworten
└── tests/
    ├── test_policy.py          # Unit (parametrisiert, jede Regel R1–R6)
    ├── test_pktline.py         # aufgezeichnete receive-pack-Bodies
    ├── test_api_proxy.py       # respx/MockTransport
    ├── test_git_e2e.py         # echtes git push gegen Wegwerf-Upstream
    ├── test_quota.py           # Fake-Clock, Sliding-Window
    └── redteam/                # docker-compose-basiert (→ 03-testing-redteam.md)
```

`policy.py`, `pktline.py`, `model.py`, `api_endpoints.py` sind transport-frei und rein — direkt unit-testbar.

---

## Ports & Netze

| Port | Netz | Zweck |
| ---- | ---- | ----- |
| `8080` | `agent-net` (`internal: true`) | API-Proxy + git Smart-HTTP |
| `9090` | `admin-net` | Healthz, Log-Viewer (read-only) |

Agent → Warden: **plain HTTP** (kein TLS auf agent-net; das einzige Geheimnis, der Upstream-Token, verlässt den Agenten nie). TLS erst Warden → gitlab.com.

---

## Agent-Konfiguration

```bash
# REST gegen den Warden statt gitlab.com:
GITLAB_API_URL=http://gitlab-warden:8080/api/v4

# git: Remote-URL bleibt kanonisch in .git/config,
# Umleitung auf den Warden steht nur in der globalen Container-git-Config:
git config --global url."http://gitlab-warden:8080/git/".insteadOf "https://gitlab.com/"
GIT_TERMINAL_PROMPT=0      # Warden fordert keine Auth

# Research/Build-Egress:
http_proxy=http://forward-proxy:3128
no_proxy=gitlab-warden
```

Der Agent hält **kein** GitLab-Token. Kein `.netrc`.

**Kanonische Remote-URL:** `.git/config` bleibt byte-identisch zum Normal-Clone (`https://gitlab.com/…`). Der `insteadOf`-Rewrite in `~/.gitconfig` wirkt nur im Container (anderes `$HOME` als auf dem Host). Damit kann der Host direkt mit seinen eigenen Credentials gegen gitlab.com arbeiten.

---

## Request-Routing

```
/git/{project:path}/info/refs           → git_proxy (GET)
/git/{project:path}/git-upload-pack     → git_proxy (POST, lesen R1)
/git/{project:path}/git-receive-pack    → git_proxy (POST, prüfen!)
/api/v4/{rest:path}                     → api_proxy (alle Methoden)
/healthz                                → (nur Port 9090)
```

---

## Policy (`policy.py`)

```python
@dataclass(frozen=True)
class Decision:
    allow: bool
    rule: str            # "R1".."R6" — fürs Audit-Log
    reason: str
    token: TokenKind     # READ | WRITE | NONE

def decide(req: ProxyRequest, state: StateView, cfg: Config) -> Decision: ...
```

**Default-deny.** Reihenfolge:
1. Projekt in `ALLOWED_PROJECTS`? sonst `Deny(R6)`.
2. git receive-pack: je Ref-Kommando Präfix / Delete-Block / Branch-Quota / Rate.
3. API GET: `Allow(R1, token=READ)`.
4. API Write: Endpoint-Match in Allowlist → Ownership-Check → Quota.

---

## API-Filter

Write-Endpoints als **Tabelle** in `api_endpoints.py` (Anpassung = Config-Edit, kein Logikumbau):

| Methode | Pfad-Template | Checks | Regel |
| ------- | ------------- | ------ | ----- |
| POST | `/projects/{id}/merge_requests` | `src_branch_prefix` | R3 |
| POST | `/projects/{id}/merge_requests/{iid}/notes` | `mr_owned_by_claude` | R3 |
| PUT | `/projects/{id}/merge_requests/{iid}` | `mr_owned_by_claude`, `not_merge_intent` | R3 |
| POST | `/projects/{id}/pipeline` | `ref_prefix` | R3 |
| PUT | `/projects/{id}/merge_requests/{iid}/merge` | `ALWAYS_DENY` | **R4** |

Alles nicht explizit Erlaubte → default-deny + Audit. Lese-GETs werden mit Read-Token durchgereicht (R1).

**Ownership-Check** (`mr_owned_by_claude`): Warden holt `GET /merge_requests/{iid}`, prüft `source_branch.startswith(BRANCH_PREFIX)` und `author.id == SERVICE_ACCOUNT_ID`. Die Service-Account-ID wird einmal beim Start gecacht (`GET /user`).

---

## git-Schreibpfad G1 (`git_proxy.py` + `pktline.py`)

Der Warden liest die pkt-line-Kommando-Sektion **vor** den PACK-Binärdaten, entscheidet, und streamt den unveränderten Body weiter. Kein bare-Repo, kein Mirror.

```
POST git-receive-pack:
  1. Stream lesen bis flush-pkt (0000)  →  Kommando-Sektion (KB, nicht MB)
  2. pkt-line parsen → RefCommand-Liste
  3. decide() für jede Ref:
     - refname beginnt mit BRANCH_PREFIX?              (R2)
     - new-oid ≠ all-zeros (kein Branch-Delete)?       (R2)
     - open_branches < max_branches?                   (R5)
     - writes_last_hour < max_writes_per_hour?         (R5)
  4. Alle allow → State schreiben + Body (Kopf + PACK-Rest) upstream streamen
     Einer deny  → git-Fehler über sideband, kein Upstream-Call
```

Force-Push (non-fast-forward) ist aus dem Request allein nicht entscheidbar → an GitLab Push Rules / Protected Branches delegiert.

Protokoll-Details → [`warden/GIT_SERVER_REFERENCE.md`](../../warden/GIT_SERVER_REFERENCE.md).

---

## State & Quoten (`state.py`)

SQLite (WAL + `synchronous=FULL`), Volume-persistent.

```sql
CREATE TABLE writes (
  id INTEGER PRIMARY KEY, ts REAL NOT NULL,
  channel TEXT NOT NULL, kind TEXT NOT NULL, ref_or_iid TEXT
);
CREATE TABLE claude_branches (project TEXT, ref TEXT, created REAL,
                              PRIMARY KEY (project, ref));
CREATE TABLE claude_mrs      (project TEXT, iid INTEGER, state TEXT, created REAL,
                              PRIMARY KEY (project, iid));
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);  -- 'last_reconcile'
```

- Rate: `SELECT count(*) FROM writes WHERE ts > now-3600`.
- **Fail-safe:** leer oder korrupt → „Limit erreicht", Writes verweigert bis Reconcile. Niemals „leer = 0 frei".
- **Reconcile beim Start** (`__main__.py`): offene Claude-MRs/Branches per API zählen, bevor Port 8080 öffnet.
- Periodischer Reconcile (alle 5 min): gleicht lokale Zähler gegen GitLab-Wahrheit ab.

---

## Credentials (`upstream.py`)

Zwei Tokens, nur im Warden-Container:

| Env | Scopes | Pfad |
| --- | ------ | ---- |
| `GITLAB_READ_TOKEN` | `read_api`, `read_repository` | REST-GET + git-upload-pack |
| `GITLAB_WRITE_TOKEN` | `api` (Service-Account/Developer) | API-Writes + git-receive-pack |

Injektion REST: `PRIVATE-TOKEN: <token>`. git Smart-HTTP: Basic-Auth `oauth2:<token>`.

Agent → Warden: **keine** Credentials (Warden fordert keine Auth). Das Netz ist die Grenze.

---

## Konfiguration (`config/warden.toml`)

```toml
branch_prefix        = "claude/"
max_open_mrs         = 5
max_open_branches    = 10
max_writes_per_hour  = 60
allowed_projects     = ["group/proj-a"]
gitlab_api_url       = "https://gitlab.com/api/v4"
gitlab_git_base      = "https://gitlab.com"
reconcile_interval_s = 300
state_db_path        = "/var/lib/warden/state.db"
audit_log_path       = "/var/log/warden/audit.jsonl"
```

Tokens kommen ausschließlich aus `.env` (niemals in `warden.toml`). Leere `allowed_projects` → Startup-Abbruch (fail-closed).

---

## Logging (`audit.py`)

Ein Schreiber (asyncio-Queue → einzelner Writer-Task), `O_APPEND`, eine vollständige JSON-Zeile pro Entscheidung. `Authorization`-Header und Token-Werte werden **nie** geloggt (Feld-Allowlist, nicht Blocklist). Rotation per rename+reopen. Fehler beim Logging → Entscheidung wird trotzdem durchgesetzt (fail-safe).

---

## Fehlerverhalten

- **Deny (API):** `403` mit `{error, rule, reason}` — keine GitLab-Antwort durchleaken.
- **Reject (git):** pkt-line `report-status` übers sideband → `git push` zeigt `! [remote rejected] … (warden: R2 …)`.
- **Warden down:** Connection-Refused (fail-closed strukturell — `agent-net` ist `internal`).
- **Upstream-5xx:** unverändert durchreichen; State nur bei bestätigtem Erfolg endgültig.

---

## Tests

| Ebene | Datei | Inhalt |
| ----- | ----- | ------ |
| Unit Policy | `test_policy.py` | parametrisierte `decide`-Fälle, alle R1–R6, Default-deny |
| git Parser | `test_pktline.py` | aufgezeichnete receive-pack-Bodies: Präfix, Delete=Null-OID, Multi-Ref, gzip |
| git E2E | `test_git_e2e.py` | echtes `git push` über den Warden, SHA-erhaltend |
| API | `test_api_proxy.py` | GET passthrough, Merge→403, Ownership-Verletzung, Token nie geleakt |
| Quoten | `test_quota.py` | N ok, N+1 blockt; Sliding-Window mit Fake-Clock |
| Red-Team | `tests/redteam/` | → [`03-testing-redteam.md`](./03-testing-redteam.md) |
