# Architektur-Generalisierung — Ideendokument

**Status: Ideensammlung / Diskussionsgrundlage — nichts hiervon ist implementiert.**

Anlass: Die aktuelle Architektur ist auf GitLab und Claude spezialisiert. Vier konkrete
Schmerzpunkte:

1. **GitLab-Kopplung** — Warden, Endpoint-Tabelle, Upstream und Reconcile sprechen nur GitLab.
2. **Fix verdrahtete Endpoints** — die erlaubten Write-Endpoints leben als Tupel in
   `warden/warden/api_endpoints.py`; von außen (Config) lässt sich nichts hinzufügen.
3. **Keine klare Trennung git vs. GitLab-API** — `git_proxy` und `api_proxy` teilen implizit
   eine Pipeline (parse → decide → record → forward → audit), aber es gibt keine Abstraktion,
   die das trägt. Ein späterer Datenbank-Guard hätte keinen Platz, an dem er andocken könnte.
4. **Claude als fester letzter Layer** — der Agent-Layer (`claude-layer/Dockerfile`) ist der
   einzige, den `catraz` kennt; andere Modelle/CLIs (Codex, OpenCode, …) sind nicht vorgesehen.

Dieses Dokument leitet zuerst die **Grundregeln** her, aus denen sich alles ableiten muss,
und entwirft dann die konkreten Generalisierungen entlang dieser Regeln.

---

## 1. Die Grundregeln (Axiome)

Der Wert des Projekts ist das Sicherheitsmodell. Jede Generalisierung ist nur dann eine
Verbesserung, wenn sie diese Axiome **strukturell erhält** — nicht per Konvention, sondern so,
dass man sie gar nicht verletzen *kann*. Die Axiome, aus denen sich der bestehende Code bereits
implizit ableitet (und die man jetzt explizit machen sollte):

### A1 — Default-Deny ist unverhandelbar
Alles nicht explizit Erlaubte ist verboten. Kein Plugin, keine Config, kein Guard darf diese
Eigenschaft aufweichen können. Konsequenz für jede Erweiterbarkeit: **Erweiterungspunkte dürfen
nur zusätzliche *Allow*-Regeln aus geprüften Bausteinen komponieren — nie Deny-Regeln entfernen
und nie neue Prüf-Logik einführen.**

### A2 — Mechanismus ist Code, Policy ist Daten
Parser, Proxy-Transport und Check-Prädikate sind Code: getestet, versioniert, reviewt.
*Welche* Endpoints mit *welchen* Checks und *welchen* Parametern erlaubt sind, ist Daten
(Tabelle/TOML). Die Endpoint-Tabelle folgt diesem Prinzip schon halb („a config edit + test,
never a logic rewrite") — sie ist nur noch nicht als Daten *ausgelagert*. Grenze: Daten dürfen
Mechanismen nur **referenzieren und parametrisieren**, niemals Code enthalten oder laden.

### A3 — Invarianten sind Code, nicht Config
Was *nie* erlaubt sein darf (Merge, Branch-Delete, Tag-Push, später: `DROP`/`GRANT`), ist
einkompiliert und von Config nicht überschreibbar oder verschattbar. Config kann enger machen,
nie weiter als die Invarianten.

### A4 — Purer Kern, unreine Ränder
`decide(intent, state, policy) → Decision` bleibt eine pure Funktion ohne I/O. Alles Unreine
(Transport, Upstream-Lookups, State, Audit) liegt an den Rändern. Das ist der Grund, warum die
Policy heute direkt unit-testbar ist — und die Eigenschaft, die jeder neue Guard erben muss.

### A5 — Eine Pipeline, vom Kernel erzwungen
Jeder Guard ist dieselbe Sequenz: **parse → enrich → decide → record → forward → audit**.
Die sicherheitskritische Reihenfolge (Deny kurzschließen; Writes *vor* dem Upstream-Call
verbuchen, §6.11; *jede* Entscheidung auditieren) lebt **einmal** im Kernel — ein neuer Guard
kann sie nicht vergessen oder falsch nachbauen.

### A6 — Credential-Isolation ist Topologie, nicht Disziplin
Der Agent hält kein Credential der bewachten Ressource; der einzige Netzweg zur Ressource
führt durch ihren Guard (fail-closed, wenn der Guard fehlt). Das generalisiert wörtlich:
*jede* neue Ressource (GitHub, Postgres, …) bekommt ihren Guard als alleinigen Credential-Halter
und der Agent strukturell keine Route daran vorbei.

### A7 — Jede Entscheidung trägt eine Regel-ID und wird auditiert
`Decision` referenziert eine Regel aus einem enumerierbaren, dokumentierten Vokabular. Kein
anonymer Allow/Deny. Bei mehreren Guards werden Regel-IDs namespaced (`gitlab.R4`, `db.R4`).

### A8 — Auflistbarkeit schlägt Ausdrucksmächtigkeit
Enumerierbare Allowlists statt Wildcards/Regex-DSLs (heutiges §6.10, `allowed_projects`).
Eine Policy-Datei muss von einem Menschen in einer Minute reviewbar sein. Das gilt auch für
jeden neuen Erweiterungspunkt: lieber zehn explizite Zeilen als ein cleveres Muster.

### A9 — Zwei unabhängige Schichten
Guard (Code, primär) + plattformnative Restriktionen (Backstop, zero-code: Protected Branches,
Token-Scopes — bzw. künftig DB-Rollen/Grants). Ein neuer Ressourcentyp ist erst vollständig,
wenn *beide* Schichten benannt sind. Wo die Plattform starke native Primitive hat (Postgres-
Rollen!), darf der native Layer sogar der primäre sein und der Guard „nur" Audit + Quoten + Mode
liefern.

### A10 — Der Agent ist austauschbare, untrusted Nutzlast
Kein Sicherheitsversprechen hängt am konkreten Modell oder CLI. Agent-spezifisch sind genau
drei Dinge: Installation (Image-Layer), Credential-Abbildung, Aufruf-Konvention. Alles davon
gehört in einen Adapter/ein Profil — die Sicherheitsarchitektur ist agent-agnostisch.

---

## 2. Das Meta-Regelwerk: R0–R6 sind Instanzen von M0–M6

Die GitLab-Regeln R0–R6 sind keine GitLab-Regeln. Sie sind Instanzen ressourcen-agnostischer
Meta-Regeln — das ist die eigentliche Entdeckung, die die Generalisierung trägt:

| Meta | Prinzip | GitLab-Instanz (heute) | Postgres-Instanz (später) |
| ---- | ------- | ---------------------- | ------------------------- |
| **M0** | Mode-Gate: `off` \| `read-only` \| `read-write` pro Ressource | `GITLAB_MODE` (R0) | `DB_MODE` |
| **M1** | Reads passieren mit einem Least-Privilege-**Read-Credential** | GET/upload-pack mit Read-Token (R1) | `SELECT` über eine read-only DB-Rolle |
| **M2** | Writes nur im **eigenen Namensraum** des Agenten | Push nur auf `branch_prefix` (R2) | `INSERT/UPDATE/DELETE` nur in Schema `agent_*` |
| **M3** | Aktionen nur auf **eigenen Objekten** (vom Service-Account erzeugt) | MR-Kommentar/Edit nur auf eigene MRs (R3) | nur eigene Rows/Tabellen (falls abbildbar) |
| **M4** | **Irreversible / privilegienerhöhende Verben: niemals** | Merge, Branch-Delete, Tag-Push (R4) | `DROP`, `TRUNCATE`, `GRANT`, `ALTER ROLE` |
| **M5** | Quoten + Rate-Limits, fail-safe bei ungeklärtem State | max MRs/Branches/Writes-pro-h (R5) | Statements/h, max Rows pro Write |
| **M6** | Credential- & Netz-Isolation + **Ressourcen-Allowlist** | kein Token im Agenten, `allowed_projects` (R6) | kein DB-Credential im Agenten, `allowed_databases` |

Damit ist auch das Regel-Vokabular (A7) definiert: neue Guards deklarieren, wie sie M0–M6
instanziieren — und was davon der native Backstop (A9) übernimmt. Ein Guard, der für eine
Meta-Regel keine Instanz *und* keinen begründeten Verzicht angibt, ist unvollständig.

---

## 3. Guard-Abstraktion: die Trennung von git- und API-Proxy

### 3.1 Ist-Zustand

`git_proxy.py` und `api_proxy.py` implementieren beide dieselbe implizite Pipeline von Hand,
inklusive der sicherheitskritischen Sequenzierung (Write-Verbuchung *vor* dem Upstream-Call,
Audit auf jedem Pfad). `policy.decide()` verzweigt per `Channel`-Enum, `ProxyRequest` ist die
Vereinigungsmenge beider Kanäle (`ref_commands` **und** `path`/`fields`/`mr_owner_ok` in einem
Objekt). Das funktioniert für zwei Kanäle — beim dritten (DB) kippt es.

### 3.2 Zielbild

Kein Framework, sondern eine dünne Schicht, die A5 erzwingt (Template-Method im Kernel):

```python
# warden/core/guard.py — der Kernel besitzt die Pipeline, Guards liefern die Teile.
class Guard(Protocol):
    name: str                          # "git", "gitlab_api", "postgres" — Audit-Namespace (A7)
    def routes(self) -> list[Route]: ...            # Transport-Anbindung
    async def parse(self, request) -> Intent: ...   # Transport → geparste Absicht
    async def enrich(self, intent) -> Intent: ...   # unreine Lookups, die Checks deklariert haben
    def decide(self, intent, state, policy) -> Decision: ...   # PUR (A4), default-deny (A1)
    def record(self, intent, state) -> None: ...    # Quota-Verbuchung VOR forward (Kernel ruft!)
    async def forward(self, request, intent, decision) -> Response: ...  # Credential-Injektion
```

Der Kernel (`run_guarded(guard, request)`) garantiert die Reihenfolge, das Mode-Gate (M0), die
Ressourcen-Allowlist (M6), das Audit auf *jedem* Ausgang und dass `forward` nur nach
`decision.allow` erreichbar ist. Guards können die Sequenz nicht falsch bauen — sie sehen sie
gar nicht.

### 3.3 Modul-Layout

```
warden/
├── core/            # Kernel: Pipeline, Decision/Intent/StateView, Audit, State, Quoten,
│   │                #   Mode-Gate, Ressourcen-Allowlist, Check-Registry, Config-Basis
├── guards/
│   ├── git/         # git Smart-HTTP (pktline, Ref-Policing) — FORGE-AGNOSTISCH
│   ├── gitlab_api/  # GitLab-REST-Endpoint-Tabelle + Ownership + Reconcile
│   ├── github_api/  # (später) GitHub-REST-Tabelle
│   └── postgres/    # (später) DB-Guard
```

Wichtige Einsicht dabei: **der git-Guard ist schon heute fast forge-agnostisch.** pktline,
Ref-Kommandos, Branch-Prefix-Policing — nichts davon ist GitLab. GitLab-spezifisch sind nur
Upstream-URL-Form und Auth-Stil (`oauth2:<token>` Basic). Die Trennung, die fehlt, ist also
nicht „git vs. GitLab" als zwei Geschwister, sondern: **git-Protokoll-Guard (generisch) +
Forge-API-Guard (pro Forge)**, die sich Namespace-Policy (`branch_prefix`), Service-Account-
Begriff und Quoten-State teilen. `upstream.py` zerfällt entsprechend in einen generischen
Streaming-Client + pro Guard einen kleinen `CredentialAdapter` (Header-/Auth-Form).

### 3.4 Intent statt Kanal-Union

`ProxyRequest` wird ersetzt durch ein gemeinsames, schlankes `Intent`-Protokoll
(`resource`, `verbs`, `objects`, `fields`) mit guard-spezifischen Ausprägungen
(`GitPushIntent(ref_commands=…)`, `ApiWriteIntent(path=…, fields=…)`,
`SqlIntent(statements=…)`). Das `Channel`-Enum und die `if/elif`-Verzweigung in
`policy.decide()` entfallen — jeder Guard bringt sein `decide` mit, der Kernel kennt nur
`Intent → Decision`.

---

## 4. Konfigurierbare Endpoints — ohne die Vertrauensgrenze zu verschieben

Der Wunsch: Endpoints von außen hinzufügen können. Die Gefahr: die Endpoint-Tabelle *ist*
Policy an der Vertrauensgrenze — als freie Config wäre sie ein Weg, die Sicherheit per Tippfehler
zu öffnen. Die Auflösung liefert A2 + A3: **zweistufige Tabelle**.

### 4.1 Check-Registry (Code, A2)

Die Prädikate werden zu benannten, parametrisierbaren Bausteinen in einer Registry:

```python
CHECKS = {
    "field_has_prefix":   lambda field, prefix=NAMESPACE: …,   # verallgemeinert src_branch_prefix/ref_prefix
    "owned_by_agent":     …,   # heutiges mr_owned_by_claude, deklariert needs={"mr_owner"}
    "field_not_equals":   …,   # verallgemeinert not_merge_intent
}
```

`src_branch_prefix` und `ref_prefix` sind heute schon dieselbe Funktion mit anderem Feldnamen —
die Registry macht das explizit. Jeder Check deklariert zusätzlich seine **Datenbedürfnisse**
(`needs = {"mr_owner"}`), die der Kernel in der `enrich`-Phase auflöst — das ersetzt die
Identitäts-Prüfung `mr_owned_by_claude in ep.checks` in `api_proxy.py:102` (Kopplung über
Funktionsobjekt-Identität).

### 4.2 Tabelle aus zwei Quellen

1. **Eingebaute Zeilen (Code):** die heutige Tabelle, inklusive aller **Deny-Invarianten**
   (Merge-Endpoint `always_deny`). Invarianten werden **immer zuerst** ausgewertet und sind
   von Config-Zeilen nicht verschattbar (A3).
2. **Nutzer-Zeilen (`warden.toml`):**

```toml
[[api.write_endpoints]]
method = "POST"
path   = "/projects/{id}/releases"
kind   = "note"                       # Quoten-Dimension (M5)
checks = [
  { check = "field_has_prefix", field = "tag_name" },   # prefix default: Namespace
]
```

### 4.3 Fail-closed-Validierung beim Start

- Unbekannter Check-Name, unbekanntes `kind`, Pfad-Kollision mit einer Invariante ⇒
  `ConfigError`, Warden startet nicht (wie heute bei kaputtem TOML).
- Jede Nutzer-Zeile **muss mindestens einen scoping Check** (Namespace M2 oder Ownership M3)
  tragen; eine check-lose Allow-Zeile ist ein Startfehler. Wer wirklich einen ungescopten
  Endpoint will, muss ein explizites, hässliches `unscoped_i_know_what_i_am_doing = true`
  setzen — grep-bar, review-bar (A8).
- Audit-Einträge aus Nutzer-Zeilen werden markiert (`rule = "gitlab.R3+user"`), damit im
  Viewer sofort sichtbar ist, welche Entscheidungen auf Nutzer-Policy beruhen.
- `catraz doctor` druckt die **effektive** Tabelle (eingebaut + Nutzer) als eine Liste.

Damit ist der Wunsch erfüllt (Endpoints von außen erweiterbar), ohne dass Config je neue
Logik einführen oder Invarianten aufweichen kann.

---

## 5. Forge-Abstraktion: GitLab → GitHub/Gitea

Mit §3 ist der Weg kurz: ein neuer Forge-API-Guard bringt seine Endpoint-Tabelle, seinen
`CredentialAdapter` und seine Reconcile-Implementierung mit; git-Protokoll-Guard, Policy-Kern,
Quoten, Audit und CLI bleiben unverändert. Konkret zu entkoppeln:

- **Begriffe:** MR vs. PR, `iid` vs. `number` — bleiben im jeweiligen Guard; der Kern kennt nur
  `kind`-Dimensionen für Quoten (M5) und Ownership (M3).
- **Reconcile** (`context.py`) ist heute GitLab-REST-spezifisch → wird Guard-Methode
  (`Guard.reconcile(state)`), der Kernel besitzt nur Takt + Lock-Semantik (§6.11).
- **Service-Account-Auflösung** (`GET /user`) → Guard-Methode mit gleichem Vertrag
  („liefere die Identität des Write-Credentials oder None ⇒ fail-closed").
- **Namensgebung im Code:** `mr_owned_by_claude` → `owned_by_agent`,
  `_list_claude_branches` → `_list_agent_branches`, „claude" bleibt ausschließlich als
  *Default-Wert* des Namespace-Präfixes. Das ist rein mechanisch, sofort machbar, und
  verhindert, dass sich die Claude-Kopplung weiter einnistet.

---

## 6. Datenbank-Guard — der Testfall, der die Abstraktion ehrlich macht

Ein Postgres-Guard nach M0–M6 (der Agent bekommt `postgres://warden:5432/`, der Guard hält
die echten Credentials):

- **M1:** `SELECT` läuft über eine **read-only DB-Rolle** — das Read-Credential *ist* der
  native Backstop (A9), exakt symmetrisch zum Read-Token bei GitLab.
- **M2:** Write-Credential hat Grants **nur** auf Schema `agent_*` — wieder nativ.
- **M4:** `DROP`/`TRUNCATE`/`GRANT`/DDL: Invariante im Code **und** fehlende Grants nativ.
- **M5 + Audit (A7):** Statements/h, jedes Statement geloggt — das kann nur der Guard.

**Ehrliche Warnung (aus A8/A9 abgeleitet):** SQL-Parsing als *primäre* Sicherheitsgrenze ist
eine Falle — prepared statements, `DO`-Blöcke, Funktionen, Dialekte. Deshalb ist beim DB-Guard
die Gewichtung anders als bei GitLab: **native Rollen/Grants sind Layer 1, der Guard liefert
Topologie (A6), Mode-Gate, Quoten und Audit** — und nur grobe Statement-Klassifikation
(read/write/ddl), keine feingranulare SQL-Policy. Genau diese Verschiebbarkeit der Gewichtung
zwischen Guard und nativem Layer muss die Abstraktion hergeben — deshalb ist der DB-Guard der
beste Test dafür, dass M0–M6 wirklich tragen und nicht nur GitLab-Regeln mit anderem Namen sind.

---

## 7. Austauschbarer Agent-Layer

Der Base-Layer ist bereits austauschbar (`BASE_IMAGE`/`BASE_DOCKERFILE`) — dasselbe Muster,
einen Schritt weitergedacht, löst den Claude-Lock-in: **Agent-Profile** statt fest verdrahtetem
`claude-layer/`.

```
src/catraz/assets/agents/
├── claude/
│   ├── layer.Dockerfile        # heutiger claude-layer
│   └── agent.toml
└── <andere>/…                  # codex, opencode, eigene
```

```toml
# agent.toml — deklarativ, was A10 als agent-spezifisch erlaubt: Install, Creds, Aufruf
name        = "claude"
command     = "claude"                          # was `catraz run` im Container startet
[credentials]
subscription_source = "~/.claude/.credentials.json"   # was `catraz sync` importiert
api_key_env         = "ANTHROPIC_API_KEY"
[egress]
domains = ["api.anthropic.com", "statsig.anthropic.com"]   # wird in die Squid-Allowlist gemergt
[modes]
remote  = true                                  # unterstützt Remote-Control-Daemon?
[logs]
debug_flag = "--debug-file"
```

- Auswahl per `.catraz/.env`: `AGENT_PROFILE=claude` (Default). Eigene Profile unter
  `.catraz/config/agents/<name>/` — gleiche Mechanik wie `BASE_DOCKERFILE`.
- **Sicherheitsvertrag unverändert:** das Profil beschreibt untrusted Nutzlast; es kann sich
  keine Warden-Credentials geben (A6). Der einzige sicherheitsrelevante Hebel ist `egress.domains`
  (Exfiltrations-Oberfläche) — deshalb druckt `catraz doctor` die effektive Egress-Liste, und
  Profile außerhalb der mitgelieferten Assets erfordern eine explizite Bestätigung bei `init`.
- CLI-Seite: `run.py`/`compose.py`/`_sync.py` lesen Kommando, Credential-Pfade und Debug-Flags
  aus dem Manifest statt aus Konstanten; `claude`-spezifische Strings verschwinden aus dem
  generischen CLI-Code. Remote-Control ist ein Claude-Feature → `modes.remote` gate-t den
  `claude-remote`-Modus pro Profil, statt ihn allen Agenten zu versprechen.
- Offene Frage: der Entrypoint (`container/entrypoint.py`) enthält Claude-Spezifika
  (CLAUDE.md-Installation, Credential-Kopie). Vorschlag: generischer Entrypoint + optionaler
  Profil-Hook (`agents/<name>/setup.py`), der als Asset mitgeliefert wird — Code aus dem
  Projekt-`.catraz/` wird dagegen **nicht** ausgeführt (A2: keine Code-Ladung aus Config).

---

## 8. Konkrete Code-Findings (unabhängig von der großen Linie)

Beim Lesen aufgefallen — jedes einzeln umsetzbar, alle verhaltens­erhaltend:

1. **Pipeline zweimal von Hand** — `api_proxy.handle` und `git_proxy.receive_pack` bauen
   Audit/record-before-forward/Deny-Kurzschluss jeweils selbst. → Kernel-Extraktion (§3.2).
2. **Kopplung über Funktions-Identität** — `api_proxy.py:102` prüft
   `mr_owned_by_claude in ep.checks`. → Checks deklarieren `needs`, Kernel enriched (§4.1).
3. **`ProxyRequest` als Kanal-Union** (`model.py`) — trägt git- und API-Felder gleichzeitig.
   → Intent-Typen pro Guard (§3.4).
4. **`Config` mischt statische Policy und Laufzeit-Cache** — `allowed_project_ids`
   (`config.py:51`) wird von `context.reconcile` per `replace(self.cfg, …)` zur Laufzeit
   ausgetauscht (`context.py:121`). → statische `Policy` von aufgelöstem Zustand trennen.
5. **`project_allowed` matcht Präfixe** (`config.py:89`: `startswith(allowed + "/")`),
   während README/Design „nur konkrete Projekte, keine Gruppen-Präfixe" doktriniert.
   Entweder exakt matchen (A8-konform) oder die Doktrin korrigieren — aktuell widersprechen
   sich Code und Dokumentation an einer sicherheitsrelevanten Stelle.
6. **Regel-IDs als Streuliteral** — `"R0"…"R6"` als bare Strings in fünf Modulen. → zentrale
   Registry (Enum + Beschreibung), vorbereitet für Guard-Namespacing (`gitlab.R4`).
7. **Audit-Schema doppelt** — `git_proxy._audit` und `api_proxy._audit` bauen ähnliche Dicts.
   → ein `AuditEvent`-Datentyp im Kern, Feld `guard` statt `channel`.
8. **Viewer-HTML inline in `app.py`** (90 Zeilen String) → statisches Package-Asset.
9. **`EndpointKind` verdrahtet Quoten-Dimensionen** — Quoten-Dimensionen (`mr.open`,
   `branch.open`, `writes.hour`) gehören als benannte Zähler in den Kern (M5), Guards mappen
   ihre Kinds darauf; dann braucht ein neuer Guard keine Kern-Änderung für eigene Quoten.
10. **`Upstream` ist GitLab-spezifisch** (PRIVATE-TOKEN, `oauth2:`-Basic, `/api/v4`-Annahme)
    → generischer Streaming-Client + `CredentialAdapter` pro Guard (§3.3).

---

## 9. Migrationspfad — kein Big Bang

Reihenfolge so gewählt, dass jeder Schritt für sich shipped werden kann und die bestehenden
Tests als Verhaltens-Netz dienen:

1. **Umbenennen** claude→agent im Warden (§5, Namensgebung) — mechanisch, risikolos.
2. **Kernel extrahieren** (§3.2) — reines Refactoring, von `test_api_proxy`/`test_git_proxy`
   abgedeckt; Findings 1, 3, 6, 7 fallen dabei mit ab.
3. **Check-Registry + `needs`-Deklaration** (§4.1) — löst Finding 2.
4. **Endpoint-Tabelle aus TOML** (§4.2/4.3) — der kleinste nutzersichtbare Gewinn, erfüllt
   den ursprünglichen Wunsch direkt.
5. **Guard-Layout** `core/` + `guards/` (§3.3) — Umzüge, keine Logikänderung.
6. **Agent-Profile in der CLI** (§7).
7. **Zweiter Forge-Guard oder DB-Guard** als Belastungsprobe der Abstraktion (§5/§6) —
   erst hier zeigt sich, ob M0–M6 tragen; vorher nicht generalisieren, was noch keinen
   zweiten Anwender hat.

---

## 10. Was man bewusst NICHT tun sollte (Anti-Ziele)

- **Kein dynamisches Plugin-Laden** (entry_points, `importlib` aus Config-Pfaden): Config darf
  nie zu Code-Ausführung führen (A2). Guards sind einkompiliert; Config wählt und parametrisiert.
- **Keine Wildcard-/Regex-Policy-DSL** in Nutzer-Config — A8 gilt auch für neue
  Erweiterungspunkte. Zehn explizite Zeilen schlagen ein cleveres Muster.
- **Kein generisches Proxy-Framework** als Selbstzweck. Die Generalität kommt aus den
  Meta-Regeln M0–M6, nicht aus unendlicher Flexibilität. Abstraktionen erst dann, wenn der
  zweite konkrete Anwender existiert (deshalb steht §6 am Ende des Migrationspfads, nicht am
  Anfang).
- **Deny-Invarianten niemals konfigurierbar machen** — auch nicht „nur für Tests", auch nicht
  hinter einem Flag (A3). Tests bauen sich ihre Tabelle im Code.
- **Die Zweischichtigkeit (A9) nicht der Bequemlichkeit opfern** — ein Guard ohne benannten
  nativen Backstop ist ein Design-Smell, kein Feature.
