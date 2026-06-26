# Warden — Umsetzungsplan

Konkretisierung der in [`README.md`](./README.md) entworfenen Architektur (Option C).
Während die README **was und warum** festlegt (Regeln R1–R6, Bedrohungsmodell,
Architekturoptionen), beschreibt dieses Dokument **wie** der Warden gebaut wird:
Projektstruktur, Modulschnitt, konkrete Code-Skizzen, Datenmodell, und die
Detailfragen, die die Umsetzung aufwirft — mit Entscheidung.

Status: **Implementierungs-Entwurf.** Stack steht (Python, §6.7 der README). Noch
nicht implementiert. Querverweise „§x" beziehen sich auf die README, sofern nicht
anders gekennzeichnet (`W§x` = dieses Dokument).

---

## W1. Umfang dieses Plans

Gebaut wird die **Start-Variante** aus README §6: ein Warden-Container, der

1. den **Lesepfad** durchreicht (REST-GET + git-`upload-pack`) mit Read-Token,
2. den **git-Schreibpfad (G1)** als transparenten Smart-HTTP-Proxy mit
   pkt-line-Ref-Inspektion durchsetzt (R2/R5-Push),
3. den **API-Schreibpfad** filtert (R3-Ownership, R4-Merge-Block, R5-Quoten),
4. **State** für Quoten/Rate hält (SQLite, durabel, fail-safe),
5. **alles auditierbar loggt** (JSONL).

**Nicht** Teil dieses Plans (README-konform): MCP-Sidecar (§6.12), Grafana/Loki
(§6.8, additiv), G3-Mirror (§6.2, nur bei Bedarf). Die GitLab-native Schicht (§7)
ist Zero-Code und wird **vor** dem Warden eingerichtet (Roadmap §10, Schritt 2) —
sie ist nicht Gegenstand dieses Code-Plans, aber Voraussetzung.

---

## W2. Projektstruktur

Neuer Top-Level-Ordner `warden/` im Repo, eigener Container, eigenes Image:

```
warden/
├── Dockerfile                  # python:3.12-slim, non-root, read-only rootfs
├── pyproject.toml              # gepinnte Deps, Lockfile/Hashes (§6.7 Dep-Disziplin)
├── warden/
│   ├── __main__.py             # uvicorn-Bootstrap, Reconcile-beim-Start (§6.11)
│   ├── app.py                  # Starlette-App, Routing API vs. git
│   ├── config.py               # Env → typisiertes Config-Objekt, Validierung
│   ├── policy.py               # decide(request, state) → Allow|Deny  ← reine Funktion
│   ├── allowlist.py            # datengetriebene Write-Endpoint-Tabelle (§6.9)
│   ├── api_proxy.py            # REST-Reverse-Proxy (GET-Passthrough + Write-Filter)
│   ├── git_proxy.py            # G1: 4 Smart-HTTP-Routen, Stream-Handling
│   ├── pktline.py              # pkt-line-Parser (receive-pack-Kommandos)  ← W7.2
│   ├── state.py                # SQLite-Zugriff: Quoten, Rate, Reconcile
│   ├── upstream.py             # httpx-Client, Token-Injektion, Projekt-Mapping
│   ├── audit.py                # JSONL-Logger, ein Schreiber, redaction
│   └── errors.py               # einheitliche Deny-/git-Fehler-Antworten
└── tests/                      # Spiegelbild von §8 (siehe W14)
    ├── test_policy.py          # Unit (parametrisiert)
    ├── test_pktline.py         # aufgezeichnete Bodies
    ├── test_api_proxy.py       # respx/MockTransport
    ├── test_git_e2e.py         # echtes git push gegen Wegwerf-Upstream
    ├── test_quota.py           # Fake-Clock
    └── redteam/                # §8.2, docker-compose-basiert
```

**Trennlinie (§6.7):** `policy.py` + `allowlist.py` + `pktline.py` sind **transport­frei
und rein** → direkt unit-testbar. `app.py`/`api_proxy.py`/`git_proxy.py`/`upstream.py`
machen HTTP/Streaming und rufen nur die reinen Funktionen. Diese Trennung ist die
Grundlage der Teststrategie und der Auditierbarkeit.

---

## W3. Laufzeit-Topologie & Schnittstellen

Konkretisierung von §6.1. Der Warden exponiert **zwei** Ports auf **getrennten** Netzen:

| Port | Netz | Zweck | Wer darf |
| ---- | ---- | ----- | -------- |
| `8080` | `agent-net` (`internal: true`) | API-Proxy **+** git Smart-HTTP | nur der Agent |
| `9090` | `admin-net` (kein Agent) | Healthz, read-only Log-Viewer (§6.8) | Host/Betreiber |

Der Agent spricht **ausschließlich** Port 8080, **plain HTTP** (kein TLS nötig: das
einzige Geheimnis — der Upstream-Token — verlässt den Agenten nie; TLS gibt es erst
Warden→gitlab.com). Die Vertrauensgrenze ist das Netz, nicht ein Transportzertifikat.

**Was der Agent konfiguriert bekommt** (Container-Env / Dateien):

```
# REST gegen den Warden statt gitlab.com
GITLAB_API_URL=http://gitlab-warden:8080/api/v4

# git remote zeigt auf den Warden; .netrc mit DUMMY-Credential (W9.3)
#   origin = http://gitlab-warden:8080/git/<group>/<project>.git
~/.netrc:  machine gitlab-warden login agent password x

# Research/Build-Egress unverändert (§6.6)
http_proxy=http://forward-proxy:3128
no_proxy=gitlab-warden
```

Der Agent hält **kein** echtes GitLab-Token (R6). Das Dummy-`.netrc`-Credential dient
nur dazu, dass `git` nicht interaktiv nach einem Passwort fragt — der Warden ignoriert
es (W9.3).

---

## W4. Request-Routing (`app.py`)

Eine Starlette-App, zwei Pfad-Präfixe:

```
/git/{project:path}/info/refs           → git_proxy.advertise   (GET)
/git/{project:path}/git-upload-pack     → git_proxy.upload_pack  (POST, lesen R1)
/git/{project:path}/git-receive-pack    → git_proxy.receive_pack (POST, prüfen!)
/api/v4/{rest:path}                      → api_proxy.handle       (alle Methoden)
/healthz                                 → (nur Port 9090)
```

`{project:path}` fängt auch Subgruppen (`gruppe/sub/projekt`). Aus dem Pfad-Segment
wird in `upstream.py` sowohl die **git-Upstream-URL** (`https://gitlab.com/<project>.git`)
als auch die **REST-Projekt-ID** (`<project>` URL-encoded, `%2F`) abgeleitet — und
gegen `ALLOWED_PROJECTS` geprüft (Default-deny bei Nichttreffer).

---

## W5. Die Policy-Kernfunktion (`policy.py`)

Herzstück, rein, ohne I/O — Signatur:

```python
@dataclass(frozen=True)
class Decision:
    allow: bool
    rule: str            # "R1".."R6" — fürs Audit-Log
    reason: str
    token: TokenKind     # READ | WRITE | NONE  (welcher Upstream-Token, wenn allow)

def decide(req: ProxyRequest, state: StateView, cfg: Config) -> Decision: ...
```

`ProxyRequest` ist die **bereits geparste** Absicht (Channel `api|git`, Methode,
Projekt, Endpoint-Match bzw. Ref-Kommandos, extrahierte Felder), `StateView` ist ein
**Schnappschuss** der Zähler (offene MRs, Branches, Writes/h) — beides ohne Netzwerk.
So ist `decide` deterministisch und tabellengetrieben testbar (§8.1).

Grundregel: **Default-deny.** Jeder `allow`-Pfad ist explizit. Reihenfolge der Prüfung:

1. **Projekt** in `ALLOWED_PROJECTS`? sonst `Deny(R6)`.
2. **Channel git, receive-pack:** je Ref-Kommando Präfix/Delete/Count/Rate (W7).
3. **Channel api, Methode GET:** `Allow(R1, token=READ)` (Pass-Through).
4. **Channel api, Schreibmethode:** Endpoint-Match in `allowlist.py`? Ownership? Quote?
   sonst `Deny`.

---

## W6. API-Filter (`api_proxy.py` + `allowlist.py`)

### W6.1 Datengetriebene Write-Allowlist (§6.9)

Die wenigen erlaubten Schreib-Endpoints als **Tabelle**, nicht als Code-Verzweigungen.
Anpassung bei v4-Änderung = Config-Edit + Test, kein Logikumbau:

```python
# allowlist.py  — (Methode, Pfad-Template, Checks, benötigtes Feld)
WRITE_ENDPOINTS = [
  # MR erstellen — nur wenn source_branch das Präfix trägt (R2/R3)
  W("POST", "/projects/{id}/merge_requests",            [src_branch_prefix],      "R3"),
  # Kommentar/Note — nur auf MR, dessen source_branch=Präfix UND Autor=Service-Account
  W("POST", "/projects/{id}/merge_requests/{iid}/notes",[mr_owned_by_claude],     "R3"),
  # MR bearbeiten (inkl. schließen) — gleiche Ownership
  W("PUT",  "/projects/{id}/merge_requests/{iid}",      [mr_owned_by_claude,
                                                          not_merge_intent],       "R3"),
  # CI triggern — Pipeline auf claude/*-Ref
  W("POST", "/projects/{id}/pipeline",                  [ref_prefix],             "R3"),
  # Merge — IMMER verboten (R4)
  W("PUT",  "/projects/{id}/merge_requests/{iid}/merge",[ALWAYS_DENY],            "R4"),
]
```

Jeder Eintrag ohne Treffer → **default-deny** + Audit. Ein neuer, unbekannter
Schreib-Endpoint wird also geblockt, nie still durchgelassen (§6.9: „Veralten sicher").

### W6.2 Ownership-Prüfung (`mr_owned_by_claude`)

Der teuerste Check: erfordert einen **Lookup** des Ziel-MR, bevor entschieden wird.

- Warden holt `GET /merge_requests/{iid}` (Read-Token) und prüft:
  `source_branch.startswith(BRANCH_PREFIX)` **und** `author.id == SERVICE_ACCOUNT_ID`.
- `SERVICE_ACCOUNT_ID` wird **einmal beim Start** via `GET /user` (Write-Token)
  ermittelt und gecacht — so unterscheidet der Warden „Claudes MRs" von fremden.
- Ergebnis kurz cachen (z. B. 30 s, per MR-IID), um Lookup-Sturm bei Kommentar-Folgen
  zu dämpfen. Cache ist reine Performance, nie sicherheitstragend (Default-deny bleibt).

### W6.3 Merge-Block (R4)

`ALWAYS_DENY` braucht keinen Lookup → `403` für **jeden** Merge-Endpoint und -Alias
(`/merge`, `merge_when_pipeline_succeeds` als Query/Body, `PUT .../merge_requests/{iid}`
mit `state_event=merge` — Letzteres fängt `not_merge_intent`). Die Red-Team-Suite
(§8.2) probiert genau diese Aliasse durch.

### W6.4 Read-Pass-Through (R1)

Jede `GET /api/v4/...` → unverändert mit **Read-Token** an gitlab.com gestreamt, Antwort
zurück. Kein Schema-Parsing (§6.9). Die Lese-**Reichweite** begrenzt nicht der Warden,
sondern die Projektmitgliedschaft des Read-Tokens (§6.10) plus die `ALLOWED_PROJECTS`-
Pfadprüfung, wo eine Projekt-ID im Pfad steht.

---

## W7. git-Schreibpfad G1 (`git_proxy.py` + `pktline.py`)

### W7.1 Die vier Routen

| Route | Token | Aktion |
| ----- | ----- | ------ |
| `GET …/info/refs?service=git-upload-pack` | READ | unverändert durchreichen |
| `POST …/git-upload-pack` (fetch) | READ | unverändert durchreichen (R1) |
| `GET …/info/refs?service=git-receive-pack` | WRITE | Advertisement durchreichen* |
| `POST …/git-receive-pack` (push) | WRITE | **Kommandos parsen, dann streamen** |

\* Das receive-pack-Advertisement listet nur Refs (Lesen) — keine Policy-Entscheidung,
aber es braucht den Write-Token, damit GitLab die Push-Capabilities mitschickt.

### W7.2 pkt-line-Parser — Kern von G1

Die Kommando-Sektion steht im Klartext **vor** den PACK-Binärdaten (§6.2). Skizze:

```python
def parse_commands(head: bytes) -> list[RefCommand]:
    """Liest die pkt-line-Kommandos bis zur ersten flush-pkt (0000).
    Erwartet genug gepufferte Bytes, um die Sektion abzudecken (W7.3)."""
    cmds, i = [], 0
    while i < len(head):
        length = int(head[i:i+4], 16)         # 4 hex Längenpräfix
        if length == 0:                        # 0000 = flush-pkt → Ende der Kommandos
            break
        line = head[i+4:i+length]
        # erstes Kommando trägt nach NUL die Capabilities:  "<oid> <oid> <ref>\0caps"
        line = line.split(b"\x00", 1)[0].rstrip(b"\n")
        old, new, ref = line.split(b" ", 2)
        cmds.append(RefCommand(old.decode(), new.decode(), ref.decode()))
        i += length
    return cmds

ZERO = "0" * 40                                # bzw. 64 für SHA-256-Repos

def check(cmd: RefCommand, state, cfg) -> Decision:
    ref = cmd.ref.removeprefix("refs/heads/")
    if not ref.startswith(cfg.branch_prefix):                 # R2
        return Decision(False, "R2", f"branch {ref!r} ohne Präfix", NONE)
    is_delete = (cmd.new == ZERO)
    is_create = (cmd.old == ZERO)
    if is_delete:                                             # R2: Löschen NIE erlaubt
        return Decision(False, "R2", "branch delete verboten", NONE)
    if is_create and state.open_branches >= cfg.max_branches: # R5
        return Decision(False, "R5", "max branches", NONE)
    if state.writes_last_hour >= cfg.max_writes_per_hour:     # R5
        return Decision(False, "R5", "rate limit", NONE)
    return Decision(True, "R2", "ok", WRITE)
```

Mehrere Ref-Kommandos in einem Push → **alle** müssen `allow` sein, sonst Reject des
gesamten Push (atomar, kein Teil-Forward).

### W7.3 Das Streaming-Manöver (der eigentliche Trick)

Der Warden muss die kleine Kommando-Sektion **lesen, entscheiden**, und bei Annahme
den **unveränderten** Body (gelesener Kopf + Rest inkl. PACK) upstream streamen — ohne
das Riesen-Packfile zu puffern:

```python
async def receive_pack(request):
    stream = request.stream()
    head, rest = await read_until_flush(stream)     # puffert NUR bis 0000 (KB-Größe)
    cmds = parse_commands(decompress_if_gzip(head)) # W7.4
    state = state_view()
    decisions = [check(c, state, cfg) for c in cmds]
    if not all(d.allow for d in decisions):
        audit(...);  return git_error_response(decisions)   # Reject übers sideband

    async def body():                # erst der gepufferte Kopf, dann der Reststrom
        yield head
        async for chunk in rest:
            yield chunk
    record_writes(cmds)              # State VOR dem Upstream-Call schreiben (§6.11)
    return await upstream.stream_post(project, "git-receive-pack",
                                      body(), token=WRITE)
```

`read_until_flush` liest inkrementell, bis die erste `0000`-flush-pkt gesehen ist
(Kommando-Sektion ist KB, nicht MB) — danach beginnt PACK und wird nur noch
durchgereicht. Bei Annahme = **derselbe** Vorgang wie Weiterleiten (§6.11 Idempotenz).

### W7.4 Content-Encoding / Transfer

- **gzip:** git komprimiert v. a. `upload-pack`-Request-Bodies; `receive-pack`-PACK ist
  bereits binär-komprimiert und wird i. d. R. **nicht** zusätzlich gzip-codiert. Defensiv
  trotzdem: ist `Content-Encoding: gzip` gesetzt, muss nur der **gepufferte Kopf** zum
  Parsen dekomprimiert werden; der Body wird unverändert (gzip bleibt gzip) durchgereicht.
- **chunked:** Nach dem Puffern ist die Gesamt-`Content-Length` unbekannt → upstream als
  `Transfer-Encoding: chunked` senden (httpx-Streaming-Body). Funktioniert für git.
- **Capabilities** (`report-status`, `side-band-64k`, `atomic`) werden **nicht** verändert
  — der Warden liest sie nur, um Fehler korrekt übers sideband zurückzumelden.

### W7.5 Grenze (aus §6.2 übernommen)

Force-Push (non-fast-forward) ist aus dem Request allein nicht entscheidbar → an
**GitLab Push Rules / Protected Branches** delegiert (Layer 2, §7.3). Für R2 zählt nur
der Branchname, und der wird vollständig im Warden-Trust-Bereich geprüft.

---

## W8. State & Quoten (`state.py`)

### W8.1 SQLite-Schema

```sql
-- gleitendes Fenster der Schreibaktionen (R5 Rate)
CREATE TABLE writes (
  id        INTEGER PRIMARY KEY,
  ts        REAL NOT NULL,          -- epoch, UTC
  channel   TEXT NOT NULL,          -- 'git' | 'api'
  kind      TEXT NOT NULL,          -- 'push' | 'mr' | 'note' | 'pipeline' | ...
  ref_or_iid TEXT
);
CREATE INDEX idx_writes_ts ON writes(ts);

-- bekannte, von Claude erzeugte Branches/MRs (Zähl-Cache, per Reconcile abgeglichen)
CREATE TABLE claude_branches (project TEXT, ref TEXT, created REAL,
                              PRIMARY KEY (project, ref));
CREATE TABLE claude_mrs      (project TEXT, iid INTEGER, state TEXT, created REAL,
                              PRIMARY KEY (project, iid));

-- Reconcile-Wasserzeichen (für Fail-safe-Logik, §6.11)
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);   -- 'last_reconcile' → ts
```

- **Rate (`MAX_WRITES_PER_HOUR`):** `SELECT count(*) FROM writes WHERE ts > now-3600`.
  Exakt (kein Token-Bucket-Approx nötig bei dieser Last). Alte Zeilen periodisch prunen.
- **Offene MRs/Branches:** `count` aus `claude_*` mit `state='opened'` bzw. Ref existent.

### W8.2 Durabilität & Fail-safe (§6.11 — verbindlich)

- **WAL + `synchronous=FULL`**, DB auf eigenem Volume; jeder Write-Record committet vor
  dem Upstream-Call → ein Crash verliert den Stundenzähler **nicht**.
- **Reconcile beim Start** (`__main__.py`): offene Claude-MRs/Branches per API zählen,
  `claude_*`-Tabellen setzen, `last_reconcile` stempeln — **bevor** Port 8080 öffnet.
- **Leer/korrupt ⇒ gesperrt:** lässt sich der State nicht rekonstruieren, gilt „Limit
  erreicht → alle Writes verweigern", bis ein Reconcile gelingt. **Nie** „leer = 0 frei".
- **Periodischer Reconcile** (Cron im Warden, z. B. alle 5 min): gleicht lokale Zähler
  gegen die GitLab-Wahrheit ab (manuell geschlossene MRs geben Kontingent frei). Das ist
  der **Backstop** (§7.8), nicht die primäre Durchsetzung — die ist der synchrone
  Pre-Write-Check in `decide`.

---

## W9. Credentials & Authentifizierung

### W9.1 Welche Tokens der Warden hält

Zwei GitLab-Tokens (§7.5), **nur** im Warden-Container (Env/Secret):

- `GITLAB_READ_TOKEN` — `read_api`, `read_repository` → Lesepfad.
- `GITLAB_WRITE_TOKEN` — `api`, Service-Account/Developer (§7.1) → API-Writes + git-push.

### W9.2 Injektion upstream

- **REST:** Header `PRIVATE-TOKEN: <token>` (oder `Authorization: Bearer`) je nach
  Token-Typ; Read- vs. Write-Token nach `Decision.token`.
- **git Smart-HTTP:** HTTP-Basic gegen gitlab.com, `Authorization: Basic
  base64("oauth2:<token>")` — Standard für Token-Auth bei GitLab über HTTPS. upload-pack
  → Read-Token, receive-pack → Write-Token.

### W9.3 Agent → Warden: keine echte Auth

Der Bedrohungsraum auf `agent-net` ist **nur der Agent selbst** (kein Multi-Tenant), und
er bekommt durch „Authentifizieren" keine zusätzlichen Rechte — der Warden poliziert
ohnehin jeden Request. Daher: Der Warden **fordert keine** Credentials vom Agenten an und
ignoriert das Dummy-`.netrc`. Begründung steht hier, damit niemand später „aus Reflex"
eine Agent-seitige Auth einbaut, die nur Scheinsicherheit wäre. (Wer den Audit-Trail je
Agent-Identität schärfen will: optional ein statisches, nicht-geheimes
`X-Agent-Id`-Header-Logging — rein informativ, nie autorisierend.)

---

## W10. Konfiguration (`config.py`)

Strikte Trennung **Secrets vs. Tunables** (vgl. README §11, „config/-Ordner"):

- **Secrets nur als Env** (aus `.env`, nie im Repo, nie im read-only `config/`):
  `GITLAB_READ_TOKEN`, `GITLAB_WRITE_TOKEN`. Niemals in der Agent-Env (R6).
- **Nicht-geheime Tunables in einer host-editierbaren Datei** `config/warden.toml`,
  **read-only** in den Warden gemountet (`/etc/warden/warden.toml`). So ändert der Nutzer
  Präfix/Limits/Projekte vom Host aus, ohne Image-Rebuild — und ohne dass je ein Secret in
  der editierbaren Datei landet.

```toml
# config/warden.toml  — NICHT-geheime Tunables (host-editierbar). KEINE Tokens hier!
branch_prefix        = "claude/"
max_open_mrs         = 5
max_open_branches    = 10
max_writes_per_hour  = 60
allowed_projects     = ["group/proj-a", "group/proj-b"]
gitlab_api_url       = "https://gitlab.com/api/v4"
gitlab_git_base      = "https://gitlab.com"
reconcile_interval_s = 300
state_db_path        = "/var/lib/warden/state.db"
audit_log_path       = "/var/log/warden/audit.jsonl"
# Die datengetriebene Write-Endpoint-Allowlist (W6.1) kann hier als [[write_endpoints]]
# stehen oder in einer Schwesterdatei config/warden-write-endpoints.toml.
```

`config.py` lädt `warden.toml` + Token-Env und validiert beim Start **hart**: fehlender
Token oder leere `allowed_projects` → **Abbruch**, nicht „offen". `allowed_projects` leer
⇒ Fail-closed (nichts erlaubt), nicht „alles". Die vollständige `docker-compose`-Einbindung
(Mounts, Netze, Secrets via `.env`) steht konsolidiert in **README §11**.

---

## W11. Logging (`audit.py`)

Setzt §6.8 um: **ein** Schreiber (asyncio-Queue → einzelner Writer-Task), `O_APPEND`,
eine vollständige JSON-Zeile pro Entscheidung, `Authorization`/Token **nie** geloggt
(Redaction-Allowlist der Felder, nicht Blocklist). Ein Audit-Eintrag pro Vorgang in
**beiden** Pfaden mit `rule`, `decision`, Quoten-Stand. Rotation per rename+reopen.
Schlägt das Logging fehl → Entscheidung wird trotzdem durchgesetzt, Fehler auf stderr
(fail-safe, nie Policy-blockierend).

---

## W12. docker-compose & Netz

> Die **vollständige, konsolidierte** `docker-compose.yml` (alle Services, Netze,
> Volumes, `config/`-Mounts, Secrets via `.env`) steht in **README §11** — sie ist die
> Quelle der Wahrheit. Hier nur die **Warden-spezifischen** Punkte:

```yaml
  gitlab-warden:
    build: ./warden
    networks: [agent-net, egress-net, admin-net]   # einzige Vertrauensgrenze
    environment:                                    # Secrets NUR via .env (W10)
      - GITLAB_READ_TOKEN=${GITLAB_READ_TOKEN}
      - GITLAB_WRITE_TOKEN=${GITLAB_WRITE_TOKEN}
    volumes:
      - ./config/warden.toml:/etc/warden/warden.toml:ro   # host-editierbar, KEINE Secrets
      - ./state/warden:/var/lib/warden                    # Bind-Mount: SQLite-State (auditierbar)
      - ./logs/warden:/var/log/warden                     # Bind-Mount: Audit-JSONL (auditierbar)
    read_only: true
    tmpfs: [/tmp]
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9090/healthz')"]
    restart: unless-stopped
```

- **Netze:** `agent-net` (`internal: true`, kein Egress), `egress-net` (Internet, nur
  Warden + Proxy), `admin-net` (Healthz/Log-Viewer auf `9090`, kein Agent).
- **`config/warden.toml`** read-only gemountet (W10); Tokens kommen aus `.env`, nicht aus
  `config/`.
- **`state/`/`logs/` als Bind-Mounts** (keine Named Volumes) → SQLite-State und Audit-JSONL
  liegen als Dateien neben dem Compose-File und sind ohne Docker-Tools auditierbar
  (`tail`/`sqlite3`); Anlegen + Ownership siehe README §11.6.

**Sofort-Schritt unabhängig vom Warden (Roadmap §10.1):** `GITLAB_API_TOKEN` (Zeile 52 im
bestehenden `docker-compose.yml`) und `GITLAB_GIT_TOKEN` aus dem `claude-dev-env`-Service
ziehen — behebt die akute R6-Verletzung aus §4, schon bevor der Warden steht (siehe
[`01-bootstrap-hardening.md`](./01-bootstrap-hardening.md)).

---

## W13. Fehler- & Ausfallverhalten

- **Deny (API):** `403` mit JSON `{error, rule, reason}` — keine GitLab-Antwort leaken.
- **Reject (git):** korrekt formatierte `report-status`-pkt-line übers side-band, sodass
  `git push` eine klare Fehlermeldung zeigt (`! [remote rejected] … (warden: R2 …)`).
- **Warden down:** Agent bekommt Connection-Refused (fail-closed strukturell, §6.11) —
  **kein** stiller Direktzugriff möglich (`agent-net` ist `internal`).
- **Upstream-5xx von gitlab.com:** unverändert durchreichen; State nur bei bestätigtem
  Write-Erfolg als endgültig behandeln (Reconcile korrigiert Drift).

---

## W14. Teststrategie → Dateien (§8)

| §8-Ebene | Datei | Kerninhalt |
| -------- | ----- | ---------- |
| Unit Policy | `test_policy.py` | parametrisierte `decide`-Fälle, jede Regel R1–R6, Default-deny |
| git Parser | `test_pktline.py` | aufgezeichnete `receive-pack`-Bodies: gut/böse Präfix, Delete=Null-OID, Multi-Ref, gzip-Kopf |
| git E2E | `test_git_e2e.py` | echtes `git push` (`tmp_path`) über den Warden gegen Wegwerf-Upstream → accept/reject, **SHA-erhaltend** |
| API | `test_api_proxy.py` | `respx`/`MockTransport`: GET passthrough, Merge→403, Ownership-Verletzung→deny, **Token nie geleakt** |
| Quoten | `test_quota.py` | N Writes ok, N+1 blockt; Sliding-Window mit injizierter Uhr (kein `sleep`) |
| Red-Team | `tests/redteam/` | §8.2-Suite: `printenv` ohne Token, kein Direkt-Connect, falsches Präfix, Merge-Aliasse, Flooding, Exfil |

Regel-IDs in Testnamen (`TestR4_MergeBlocked`), Coverage-Gate ≥ 90 % auf `policy.py`,
Log-Assertions (jede Entscheidung erzeugt redacteten Audit-Eintrag).

---

## W15. Detailfragen — Entscheidungen

Fragen, die die Umsetzung aufwirft, mit getroffener Entscheidung. ✅ = entschieden,
⚠️ = entschieden mit Vorbehalt/Verifikationspunkt, ❓ = braucht deine Bestätigung.

| # | Frage | Entscheidung |
| - | ----- | ------------ |
| Q1 | Auth Agent→Warden? | ✅ **Keine.** Netz ist die Grenze; Agent gewinnt durch Auth nichts (W9.3). Dummy-`.netrc` nur gegen git-Prompt. |
| Q2 | TLS Agent↔Warden? | ✅ **Plain HTTP** auf `agent-net`. TLS erst Warden→gitlab.com. Kein Geheimnis auf der internen Strecke. |
| Q3 | Darf der Agent eigene `claude/*`-Branches **löschen**? | ✅ **Nein, nie.** Delete (new-oid = Null) → immer reject (R2), konform zu §6.2. Aufräumen offener Branches macht ein Mensch oder ein separater, nicht vom Agenten gesteuerter Job. |
| Q4 | Read- vs. Write-Token-Wahl | ✅ Per Route/Decision: GET+upload-pack → READ; receive-pack+erlaubte API-Writes → WRITE. |
| Q5 | Ownership „gehört Claude"? | ✅ `source_branch`-Präfix **und** `author.id == SERVICE_ACCOUNT_ID` (beim Start gecacht). |
| Q6 | gzip-`receive-pack`-Body | ✅ Nur den gepufferten Kopf zum Parsen dekomprimieren, Body unverändert durchreichen (W7.4). |
| Q7 | Rate-Limit-Mechanik | ✅ Exakter Zeitstempel-Zähler in SQLite (kein Token-Bucket-Approx) — Last ist niedrig. |
| Q8 | Quoten-Zähler-Wahrheit | ✅ Lokal optimistisch inkrementieren, periodisch + beim Start gegen API reconcilen; leer/korrupt ⇒ gesperrt (§6.11). |
| Q9 | Projekt-Allowlist-Granularität | ✅ Pfad-Präfix-Match auf `ALLOWED_PROJECTS`; leer ⇒ fail-closed. Read-Reichweite zusätzlich über Token-Scope (§6.10). |
| Q10 | Mehrere Ref-Kommandos pro Push | ✅ Atomar: ein verbotenes Kommando ⇒ ganzer Push abgelehnt. |
| Q11 | Force-Push hart Warden-seitig? | ⚠️ **Nein** in G1 (nicht entscheidbar) → an Push Rules delegiert (§7.3). Wer es zwingend will: G3 (§6.2). |
| Q12 | Sprache/Framework | ✅ Python, Starlette + httpx + sqlite3 (§6.7), `__main__` via uvicorn. |

**Offene Verifikationspunkte (vor Inbetriebnahme, aus §9):**

- ⚠️ GitLab-**Basic-Auth-Form** für Token über Smart-HTTP (`oauth2:<token>`) im
  konkreten Setup verifizieren (W9.2).
- ⚠️ **Push-Rules-/Protected-Branch-Semantik** auf gitlab.com (§7, §9) — Backstop für Q11.
- ⚠️ **RC-Token-Fähigkeiten** des Claude-Kontos (§3.2) — orthogonal zum Warden, aber Teil
  derselben Inbetriebnahme-Checkliste.

---

## W16. Umsetzungsreihenfolge (Warden-intern)

Spiegelt Roadmap §10, hier auf Module heruntergebrochen:

1. Gerüst: `config.py` (+ harte Validierung), `app.py`, `audit.py`, `state.py`-Schema,
   Reconcile-beim-Start, Healthz — **Default-deny von Anfang an**.
2. **Lesepfad:** `api_proxy` GET-Passthrough + `git_proxy` upload-pack/info-refs. R1
   verifizieren (`test_api_proxy`, manuelles `git clone`/`fetch` über den Warden).
3. **git-Schreibpfad (G1):** `pktline.py` + `git_proxy.receive_pack` + Streaming-Manöver.
   `test_pktline` + `test_git_e2e` grün, SHA-Gleichheit Host-Clone↔Server geprüft.
4. **API-Schreibpfad:** `allowlist.py` + Ownership + Merge-Block + R5-Hard-Limit.
   `test_policy` + `test_quota` grün.
5. **Red-Team-Suite** (`tests/redteam/`) grün, in CI verankert (§8.4).
6. Transparenz-Ausbau (Log-Viewer/Grafana, §6.8) — additiv.
