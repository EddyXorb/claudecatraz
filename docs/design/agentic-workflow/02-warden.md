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
│   ├── config.py               # das typisierte, frozen Config-Objekt (Modell-Hälfte)
│   ├── config_load.py          # Env + warden.toml → Config, harte fail-closed Validierung
│   ├── model.py                # Policy-Datentypen (pure)
│   ├── rules.py                # zentrale Regel-Registry R0–R6 + Meta-Regel-Zuordnung (B3, F11)  ← pure
│   ├── capabilities.py         # Capability-Vokabular + FORBIDDEN + forbidden_check (§03.4, B2)  ← pure
│   ├── policy.py               # decide(request, state, cfg) → Decision  ← pure
│   ├── api_endpoints.py        # Kompat-Fassade auf warden.catalog (§06 Schritt 4)  ← pure
│   ├── catalog/                # Endpoint-Katalog + Check-Registry + Aktivierung (§04, Schritt 4)
│   │   ├── model.py            #   CatalogEntry/FieldSpec/DenyProbe/OverridableParam  ← pure
│   │   ├── checks.py           #   Check-Registry (§04.1): field_has_prefix, owned_by_agent, …  ← pure
│   │   ├── entries.py          #   CATALOG-Tabelle (§04.2) + api_capabilities/match_endpoint  ← pure
│   │   ├── builtin.py          #   Merge-Endpoint: eingebaute Deny-Invariante, kein Katalog-Eintrag  ← pure
│   │   ├── config_parse.py     #   [api.endpoints]-TOML-Form parsen (kein Config-Import)  ← pure
│   │   ├── activation.py       #   Config × Katalog → effektive Tabelle (§04.3), fail-closed  ← pure
│   │   ├── startgate.py        #   Deny-Sonden gegen die effektive Policy beim Start (§04.4)
│   │   └── report.py           #   /policy-Admin-Route: JSON-Report der effektiven Tabelle
│   ├── read_endpoints.py       # datengetriebene Read-Endpoint-Tabelle (B1)  ← pure
│   ├── path_template.py        # {platzhalter}-Pfad → Regex, von Katalog+Read-Tabelle genutzt  ← pure
│   ├── api_proxy.py            # REST-Reverse-Proxy (GET-Passthrough + Write-Filter)
│   ├── git_proxy.py            # G1: 4 Smart-HTTP-Routen, Stream-Handling
│   ├── pktline.py              # pkt-line-Parser (receive-pack-Kommandos)  ← pure
│   ├── state.py                # SQLite-Zugriff: Quoten, Rate, Reconcile, Schema-Versionierung
│   ├── context.py              # Runtime-Ctx + Reconcile-Logik
│   ├── upstream.py             # httpx-Client, Token-Injektion
│   ├── audit.py                # JSONL-Logger, ein Schreiber, Redaction, `schema`-Feld
│   └── errors.py               # Deny-/git-Fehler-Antworten
└── tests/
    ├── test_rules.py           # Registry + "jede geloggte ID ist registriert"
    ├── test_capabilities.py    # Golden-Tests: Intent → Capability-Menge, FORBIDDEN-Invariante
    ├── test_policy.py          # Unit (parametrisiert, jede Regel R0–R6)
    ├── test_pktline.py         # aufgezeichnete receive-pack-Bodies
    ├── test_api_proxy.py       # respx/MockTransport
    ├── test_git_e2e.py         # echtes git push gegen Wegwerf-Upstream
    ├── test_quota.py           # Fake-Clock, Sliding-Window
    ├── test_state.py           # inkl. Schema-Migration (frisch/legacy/zu neu)
    └── redteam/                # docker-compose-basiert (→ 03-testing-redteam.md)
```

`policy.py`, `pktline.py`, `model.py`, `capabilities.py`, `catalog/` (bis auf `startgate.py`, das `policy.decide` aufruft, aber selbst keine Ein-/Ausgabe hat), `read_endpoints.py`, `path_template.py` sind transport-frei und rein — direkt unit-testbar.

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
/api/graphql, /api/graphql/{rest:path}  → api_proxy.deny_graphql (immer 403, B5)
/healthz                                → (nur Port 9090)
/policy                                 → catalog.report (nur Port 9090, read-only, §04.3)
```

GraphQL wird nie an den Upstream durchgereicht (B5, `docs/design/architecture-generalization/02-befunde.md`): eine einzige Mutation dort könnte alles, was der REST-Schreibfilter verbietet.

---

## Regel-Registry (`rules.py`)

Seit §06-migration.md Schritt 2 (B3, F11) sind R0–R6 in `rules.py` zentral definiert —
ID, zugeordnete Meta-Regel (M0–M6, §01-grundregeln.md B) und Kurzbeschreibung. `policy.py`,
`api_endpoints.py`, `read_endpoints.py`, `api_proxy.py` und `git_proxy.py` referenzieren
diese Konstanten statt Streuliteralen. Ein reservierter Kernel-Namespace (`core.*`, neben
`gitlab.*` für diesen Guard) ist als Helfer (`rules.qualify`) vorbereitet, aber noch nicht
aktiv — geloggt wird weiterhin die unqualifizierte Form (`"R4"`), bis der
channel→guard-Rename (F11) landet und ein zweiter Guard (§06 Schritt 9) unqualifizierte
IDs mehrdeutig macht.

| Regel | Meta | Bedeutung |
| ----- | ---- | --------- |
| R0 | M0 | Mode-Gate — GitLab aus oder Writes aus |
| R1 | M1 | Read-Passthrough mit dem Read-Token |
| R2 | M2 | Write nur im eigenen Branch-Namensraum |
| R3 | M3 | Write nur auf eigenen (selbst erstellten) Objekten |
| R4 | M4 | Irreversibler Verb, niemals: Merge, Tag-Push, Branch-Delete |
| R5 | M5 | Quote/Rate-Limit, fail-safe bei ungeklärtem State |
| R6 | M6 | Ressourcen-Allowlist-Grenze (Projekt/Credential-Scope) |

**B3-Fix:** Tag-Push und Branch-Delete liefen zuvor unter R2 (Branch-Namensraum), obwohl sie
konzeptionell „irreversible Verben: niemals" (M4) sind — dieselbe Kategorie wie der
Merge-Block. Beide loggen jetzt R4 (`policy.check_ref`). Das ist eine audit-sichtbare
Änderung, deshalb an die Schema-Versionierung dieses Schritts gekoppelt (`audit.AUDIT_SCHEMA_VERSION`).

---

## Capability-Invarianten-Ebene (`capabilities.py`)

Seit §06-migration.md Schritt 3 (§03.4, B2). Jeder Kanal normalisiert seinen bereits
geparsten Intent zusätzlich auf ein kleines, **geschlossenes** Capability-Vokabular —
was der Request *bewirken würde*, unabhängig davon, wie er es sagt:

```
creates_ref · deletes_ref · creates_tag · merges · escalates_privilege ·
writes_outside_namespace · destroys_data
```

Eine einzige einkompilierte `FORBIDDEN`-Menge (`deletes_ref`, `creates_tag`, `merges`,
`escalates_privilege`, `destroys_data`) wird von `forbidden_check` geprüft — **niemals
konfigurierbar** (§06.2). `creates_ref` fehlt bewusst (Normalfall des Agenten);
`writes_outside_namespace` fehlt bewusst (der Namensraum ist `Config.branch_prefixes`,
pro Deployment konfigurierbar — R2/R3 bändigen das per Request, keyed auf die jeweils
konfigurierten Präfixe; ein kompiliertes Pauschalverbot würde diesen Check bekämpfen
statt ergänzen). `escalates_privilege`/`destroys_data` haben heute noch keinen
Erzeuger (kein DROP-TABLE-artiger GitLab-Call) — vorbereitet für §03.7 (Postgres-Guard:
DDL/GRANT).

**Intent→Capability-Abbildung pro Kanal:**
- **git** (`capabilities.git_ref_capabilities`): trivial und exakt aus `RefCommand`
  abgeleitet — Delete (jeder Ref-Typ) ⇒ `deletes_ref`; nicht-löschender Push auf
  `refs/tags/*` ⇒ `creates_tag`; sonst Branch-Write: `creates_ref` bei Create, plus
  `writes_outside_namespace` außerhalb `branch_prefixes`.
- **REST** (`api_endpoints.api_capabilities`): jede `WriteEndpoint`-Zeile deklariert
  statische `capabilities` (vom Katalog-Autor im Code, nie vom Nutzer, §06.2). Die eine
  Ausnahme ist feld-abhängig: `PUT .../merge_requests/{iid}` trägt statisch die leere
  Menge (die Zeile editiert auch Titel/Beschreibung), aber `api_capabilities` fügt
  `merges` hinzu, sobald `fields["state_event"] == "merge"` — derselbe Alias, den
  `not_merge_intent` bereits prüft, jetzt zusätzlich strukturell in der Capability-Ebene
  verankert.

**Reihenfolge in `policy.decide`:** in `_decide_git`/`_decide_api` wird die Capability-
Menge des Intents **vor** den Endpoint-/Ref-Checks gegen `FORBIDDEN` geprüft — ein Treffer
denied sofort mit R4, unabhängig davon, was die kanalspezifischen Checks darunter
entschieden hätten. Die bestehenden Spezialfälle (`always_deny` in der Merge-Zeile,
Tag-/Delete-Checks in `check_ref`) bleiben als Defense-in-depth (A10) bestehen — die
Invariante muss aber auch ohne sie greifen (golden-getestet in `test_capabilities.py`,
u. a. mit einer hypothetischen Endpoint-Zeile ganz ohne Checks).

---

## Policy (`policy.py`)

```python
@dataclass(frozen=True)
class Decision:
    allow: bool
    rule: str            # bare rule id ("R1".."R6"), Quelle: rules.py — fürs Audit-Log
    reason: str
    token: TokenKind     # READ | WRITE | NONE

def decide(req: ProxyRequest, state: StateView, cfg: Config) -> Decision: ...
```

**Default-deny.** Reihenfolge:
1. Projekt in `ALLOWED_PROJECTS`? sonst `Deny(R6)`.
2. git receive-pack: je Ref-Kommando **Capability-Invariante** (`FORBIDDEN`-Schnitt, R4,
   §03.4) zuerst, dann Präfix (`R2`) / Tag-Push- und Delete-Block (`R4`, Defense-in-depth) /
   Branch-Quota / Rate (`R5`).
3. API GET: Projekt im Pfad → `Allow(R1, token=READ)` wie bisher; **kein** Projekt im
   Pfad → Tabellen-Match gegen `read_endpoints.py` (B1, „Inhalt, nicht Sichtbarkeit"):
   Metadaten (Projekt-/Gruppennamen, `/users`, `/version`, …) → `Allow(R1)`; inhaltsfähige
   projektlose Endpoints (globale/Gruppen-Suche mit `scope=blobs|commits|wiki_blobs|notes`,
   `/snippets`) und unbekannte projektlose Pfade → `Deny(R6)`.
4. API Write: Endpoint-Match in Allowlist → **Capability-Invariante** (`FORBIDDEN`-Schnitt,
   R4, §03.4) → Ownership-Check → Quota.

---

## Endpoint-Katalog + Aktivierung + Startgate (`catalog/`, §04, Schritt 4)

*Der nutzersichtbare Gewinn von §06-migration.md Schritt 4 — Details:
[`04-policy-erweiterbarkeit.md`](../architecture-generalization/04-policy-erweiterbarkeit.md).*

**Check-Registry (§04.1, `catalog/checks.py`).** Benannte, parametrisierbare Bausteine statt
verstreuter Einzelfunktionen: `field_has_prefix(field)` (vereinigt die alten
`src_branch_prefix`/`ref_prefix`, F10), `owned_by_agent` (früher `mr_owned_by_claude`, jetzt ein
`RegisteredCheck` mit `needs={"mr_owner"}`), `field_not_equals(field, value)` (verallgemeinert
`not_merge_intent`). `needs` ersetzt die alte Identitätsprüfung
`mr_owned_by_claude in ep.checks` (F2) — `api_proxy._resolve_ownership` fragt stattdessen
`any("mr_owner" in check.needs for check in ep.checks)`.

**Der Katalog (§04.2, `catalog/entries.py`).** Jeder Eintrag (`CatalogEntry`) trägt Methode +
Pfad-Template, `checks` aus der Registry, `rule` (R-ID), `kind` (Quoten-Dimension),
`capabilities` (§03.4, code-deklariert, nie vom Nutzer), `decision_fields` (pro Feld die Lage
Body/Query, F12) und `deny_probes` (§04.4). `DEFAULT_ENABLED` ist exakt der vor Schritt 4 aktive
Sechser-Satz — Verhaltenserhaltung bei fehlender `[api.endpoints]`-Sektion:

| ID | Methode | Pfad-Template | Checks | Regel | Capabilities |
| -- | ------- | -------------- | ------ | ----- | ------------ |
| `mr.create` | POST | `/projects/{id}/merge_requests` | `field_has_prefix(source_branch)` | R3 | ∅ |
| `mr.note` | POST | `/projects/{id}/merge_requests/{iid}/notes` | `owned_by_agent` | R3 | ∅ |
| `mr.discussion` | POST | `/projects/{id}/merge_requests/{iid}/discussions` | `owned_by_agent` | R3 | ∅ |
| `mr.discussion_reply` | POST | `/projects/{id}/merge_requests/{iid}/discussions/{discussion_id}/notes` | `owned_by_agent` | R3 | ∅ |
| `mr.update` | PUT | `/projects/{id}/merge_requests/{iid}` | `owned_by_agent`, `field_not_equals(state_event, merge)` | R3 | ∅ statisch, `+merges` bei `state_event=merge` |
| `pipeline.trigger` | POST | `/projects/{id}/pipeline` | `field_has_prefix(ref)` | R3 | ∅ |

Zusätzlich, **ehrlich katalogisiert, aber nicht im Default-Satz**:

| ID | Methode | Pfad-Template | Checks | Regel | Capabilities |
| -- | ------- | -------------- | ------ | ----- | ------------ |
| `branch.create` | POST | `/projects/{id}/repository/branches` | `field_has_prefix(branch)` | R3 | `{creates_ref}` |
| `issue.create` | POST | `/projects/{id}/issues` | — | R3 | ∅ |

Die Merge-Zeile (`PUT .../merge_requests/{iid}/merge`) ist **kein Katalog-Eintrag** —
`catalog/builtin.py` matcht sie als eingebaute Deny-Invariante, unabhängig vom
Aktivierungszustand, bevor die effektive Tabelle überhaupt konsultiert wird. Kein
`[api.endpoints]`-Eintrag kann sie je aktivierbar machen.

**Aktivierung (§04.3, `catalog/config_parse.py` + `catalog/activation.py`).**
`warden.toml`:

```toml
[api.endpoints]
enable = ["mr.create", "mr.note", "mr.discussion", "mr.discussion_reply",
          "mr.update", "pipeline.trigger", "branch.create"]   # heutiger Default + branch.create

[api.endpoints.overrides."branch.create"]
branch_prefix = "claude/x-"     # nur Verengung erlaubt (muss in Config.branch_prefixes liegen)
```

Fehlt die Sektion, gilt `DEFAULT_ENABLED` (Verhaltenserhaltung). `build_effective_table`
(Config × Katalog → `EffectiveTable`) läuft **einmal** beim Start
(`Config.effective_endpoints`, memoisiert) und bricht mit `ConfigError` ab bei: unbekannter
Katalog-ID (in `enable` oder `overrides`), Override für einen nicht-aktivierten Eintrag,
Override ohne passenden `OverridableParam`, Override, der erweitert statt verengt, oder
Aktivierung eines Eintrags, dessen `capabilities` die `FORBIDDEN`-Menge schneiden (§04.2
YAGNI — kein Taming-Mechanismus in diesem Schritt, siehe
[`04-policy-erweiterbarkeit.md`](../architecture-generalization/04-policy-erweiterbarkeit.md)).
`policy._decide_api` und `api_proxy._parse_request` matchen ausschließlich gegen
`cfg.effective_endpoints.entries` — nie gegen `catalog.CATALOG` direkt.

**F12-Fix:** `api_proxy._extract_fields` liest für einen gematchten Katalog-Eintrag nur die in
`decision_fields` deklarierten Felder, jeweils exakt aus der deklarierten Lage (Body/Query) —
kein Merge mehr. Ein `source_branch`, das nur als Query-Parameter mitgeschickt wird, zählt für
die Entscheidung als **nicht gesetzt**, obwohl das Forwarding den Querystring unverändert
weiterreicht (wie seit Schritt 1).

**Audit-Markierung.** Ein Eintrag, der über die Default-Menge hinaus aktiviert wurde, bekommt im
Audit-Event ein zusätzliches Feld `enabled_via = "config:<id>"` (additiv, kein
`AUDIT_SCHEMA_VERSION`-Bump — siehe Doku-Notiz). Default-aktivierte Einträge tragen das Feld gar
nicht.

**Startgate (§04.4, `catalog/startgate.py`).** Nach Config-Validierung, vor dem Öffnen der
Ports (`__main__.py`): für jeden aktivierten Katalog-Eintrag laufen seine `deny_probes` gegen
`policy.decide` mit einer synthetischen, entsperrten `StateView` — kein Netz, keine State-DB.
Eine Sonde, die **erlaubt** würde, wirft `StartgateFailure` (Prozess-Exit 2, wie `ConfigError`).
Zusätzlich laufen zwei globale Sonden der eingebauten Invarianten (`catalog.builtin.BUILTIN_DENY_PROBES`):
der Merge-Endpoint, und `state_event=merge` unabhängig davon, ob `mr.update` aktiv ist.

**Ownership-Check** (`owned_by_agent`): Warden holt `GET /merge_requests/{iid}`, prüft
`source_branch.startswith(BRANCH_PREFIX)` und `author.id == SERVICE_ACCOUNT_ID`. Die
Service-Account-ID wird einmal beim Start gecacht (`GET /user`).

Alles nicht explizit Erlaubte → default-deny + Audit. Lese-GETs werden mit Read-Token durchgereicht (R1).

---

## git-Schreibpfad G1 (`git_proxy.py` + `pktline.py`)

Der Warden liest die pkt-line-Kommando-Sektion **vor** den PACK-Binärdaten, entscheidet, und streamt den unveränderten Body weiter. Kein bare-Repo, kein Mirror.

```
POST git-receive-pack:
  1. Stream lesen bis flush-pkt (0000)  →  Kommando-Sektion (KB, nicht MB)
  2. pkt-line parsen → RefCommand-Liste
  3. decide() für jede Ref:
     - ref ist ein Tag (refs/tags/…)? → sofort deny    (R4, irreversibler Verb)
     - refname beginnt mit BRANCH_PREFIX?              (R2)
     - new-oid ≠ all-zeros (kein Branch-Delete)?       (R4, irreversibler Verb; B3-Fix, vormals R2)
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

### Schema-Versionierung (§06-migration.md Schritt 2)

Die DB trägt ihre Schema-Version in SQLite's eigenem `PRAGMA user_version` — ein von
SQLite reservierter Integer-Slot, immer vorhanden (Default 0), braucht keine eigene
Bootstrap-Tabelle. Die bereits existierende `meta`-Tabelle bleibt für *Anwendungs*-Zustand
(`last_reconcile`); die Schema-Version ist eine *strukturelle* Tatsache, die vor jedem
Tabellenzugriff geprüft wird — dafür ist `PRAGMA user_version` der richtige Ort, eine
Vermischung mit `meta` würde beides verwischen.

Ein kleiner Migrations-Runner (`state.MIGRATIONS`, geordnete `Migration`-Liste mit
benannter Apply-Funktion) hebt eine DB Version für Version an:

- **Version 1** (implizit): das historische, unversionierte Schema — `claude_branches`/
  `claude_mrs` existieren bereits, aber kein Versions-Marker.
- **Version 2** (dieser Schritt): führt nur den Versions-Marker selbst ein — keine
  Tabellen-Umbenennung (die kommt in Schritt 6, claude→agent/F11), der Runner trägt aber
  bereits die Form, die eine solche Migration braucht.
- Eine frische DB wird direkt auf der aktuellen Version angelegt; eine bestehende
  unversionierte DB wird ohne Datenverlust hochgezogen; eine **zu neue** Version (aus einer
  neueren Warden-Version) führt zu einem harten Fehler beim Start (`state.SchemaError`,
  fail-closed, A9) — kein stilles Weiterlaufen mit unbekanntem Schema.

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

# optional (§04.2/04.3) — fehlt die Sektion, gilt der Default-Satz:
[api.endpoints]
enable = ["mr.create", "mr.note", "mr.discussion", "mr.discussion_reply",
          "mr.update", "pipeline.trigger"]
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
| API | `test_api_proxy.py` | GET passthrough, Merge→403, Ownership-Verletzung, Token nie geleakt, F2/F12/§04.3-Audit-Marker |
| Quoten | `test_quota.py` | N ok, N+1 blockt; Sliding-Window mit Fake-Clock |
| Katalog | `tests/catalog/test_config_parse.py` | `[api.endpoints]`-Formvalidierung (fail-closed) |
| Katalog | `tests/catalog/test_activation.py` | Aktivierung/Overrides: Default-Satz, unbekannte ID, Erweiterung↯, Verengung✓, FORBIDDEN↯ |
| Katalog | `tests/catalog/test_startgate.py` | jede Katalog-Sonde hält; eine durchkommende Sonde ⇒ `StartgateFailure` |
| Katalog | `tests/catalog/test_config_integration.py` | `Config.effective_endpoints` end-to-end über `from_env` |
| Red-Team | `tests/redteam/` | → [`03-testing-redteam.md`](./03-testing-redteam.md) |
