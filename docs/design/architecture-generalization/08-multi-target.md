# 08 — Design-Spike: Multi-Target (mehrere git-/Forge-Instanzen pro `.catraz`)

Pflicht-Vorlauf für §07 Punkt 8 (`07-offene-verbesserungen.md`, Abschnitt „8.
Multi-Target"), bevor dort Code entsteht. Der „Empfohlene Ansatz" im Plandokument
ist bereits vorentschieden (Host-basiertes Routing, Allowlist in `warden.toml`
unter `[git.urls] hosts = [...]`, Credentials bleiben in Env). Dieser Spike
bestätigt ihn, entscheidet die zwei offen gelassenen Detailfragen und deckt eine
dritte, im Plandokument nicht explizit benannte Frage auf (State-Keying bei
kollidierenden Projektnamen über Hosts hinweg), die für eine korrekte
Umsetzung ebenfalls entschieden werden muss.

Ergebnis vorab: der Spike selbst ist vollständig entschieden (kein „TBD" bleibt
offen). Die **Code-Umsetzung** in diesem Arbeitsschritt bleibt bewusst auf den
sicher isolierbaren, additiven Teil beschränkt (Abschnitt „Umsetzungsschnitt
dieser PR" unten) — der Rest ist als Folgearbeit benannt, nicht implementiert.

---

## 1. Bestätigung: Host-basiertes Routing

Bestätigt wie im Plandokument beschrieben, keine Änderung am Grundmechanismus:

- Der Agent behält **kanonische** Remotes (`git clone https://my-gitlab.de/x.git`,
  `GITLAB_URL=https://my-gitlab.de`). Kein `insteadOf`-Pfad-Präfix-Trick.
- Docker-DNS/Compose (`extra_hosts` bzw. Netzwerk-Aliase) zeigt jeden gelisteten
  Hostnamen auf den einen Warden-Container. Das ist reine CLI-/Compose-Topologie
  (heute in `src/catraz/assets/compose/docker-compose.yml` + `entrypoint.py` +
  `git_routing.py`, dort setzt der `insteadOf`-Rewrite den *einen* konfigurierten
  `GITLAB_URL` auf `gitlab-warden` um) — **nicht** Teil des Warden-Python-Pakets.
- Der Warden liest `request.headers["host"]`, prüft ihn gegen die Allowlist aus
  `warden.toml` (`[git.urls] hosts`) und wählt anhand des Hosts den passenden
  Upstream (Basis-URL + Credentials). Nicht gelistete Hosts: default-deny.
- Ein Warden-Prozess für alle Hosts (kein Container/Guard pro Host „auf
  Vorrat"), analog zur bestehenden Ein-Prozess-Architektur.

## 2. Offene Detailfrage 1 — API-Multi-Endpoint-Routing

**Frage:** Wie werden mehrere GitLab-**API**-Instanzen adressiert — dasselbe
Host-Routing wie bei git, oder separate Guard-Instanzen pro Host?

**Entscheidung: identisches Host-Routing, keine separaten Guard-Instanzen.**

Begründung:
- Der API-Guard (`ApiGuard`) ist bereits (§07 Punkt 6) transport-neutral: er
  hält einen injizierten `Upstream` und kennt keine forge-eigene Identität.
  Eine zweite `ApiGuard`-Instanz pro Host würde lediglich denselben Code mit
  einem anderen `Upstream` und eigenem Katalog-Zustand duplizieren — reiner
  Overhead, keine Isolationsgewinn (derselbe Prozess, dieselbe Trust-Boundary).
- Ein **einzelner** `ApiGuard` (und `GitGuard`), der pro Request den
  Ziel-Host aus dem `Host`-Header auflöst und den dazu gehörenden `Upstream`
  wählt, ist die kleinere Änderung und bleibt konsistent mit dem
  Guard-Unabhängigkeits-Ziel aus Schritt 6 (ein Guard, mehrere austauschbare
  Transporte statt mehrerer Guard-Kopien).
- REST-Pfade (`/api/v4/...`) sind je nach Host identisch strukturiert (GitLab
  API-Schema) — der Katalog (Recognizer, Capabilities, Scopes) bleibt
  **host-unabhängig**, nur der physische Transport (Basis-URL + Token)
  variiert pro Host. Es gibt also keinen fachlichen Grund, den Katalog zu
  vervielfachen.
- Folge für den Guard: `ApiGuard`/`GitGuard` werden von „hält **einen**
  `Upstream`" zu „hält eine **Abbildung** `Host → Upstream`, aufgelöst pro
  Request über den `Host`-Header, default-deny bei Miss." Das ist eine
  Erweiterung der bestehenden `__init__`-Signaturen, keine neue Guard-Klasse.

## 3. Offene Detailfrage 2 — Credentials pro Host

**Frage:** Woher kommen Token je Host (getrennte Env-Variablen pro Host?
Sektion in `warden.toml`)?

**Entscheidung: getrennte Env-Variablen pro Host, benannt nach einem
deterministischen Host-Slug; die bestehenden `GITLAB_READ_TOKEN`/
`GITLAB_WRITE_TOKEN` (+ `_FILE`-Varianten) bleiben als Alias für den
**ersten** in `[git.urls] hosts` gelisteten Host erhalten (Rückwärtskompatibilität
für bestehende Single-Host-Deployments — kein Migrationszwang).**

Konkret, für einen weiteren Host `my-gitlab.de`:

```
GITLAB_READ_TOKEN__MY_GITLAB_DE=...
GITLAB_WRITE_TOKEN__MY_GITLAB_DE=...
# _FILE-Varianten analog: GITLAB_READ_TOKEN__MY_GITLAB_DE_FILE=/run/secrets/...
```

Slug-Regel: Host in Kleinbuchstaben, jedes Zeichen außer `[a-z0-9]` wird zu
`_`, das Ergebnis in Großbuchstaben (`my-gitlab.de` → `MY_GITLAB_DE`). Rein
mechanisch, keine Kollisionsauflösung nötig, solange die Host-Allowlist keine
zwei Hosts enthält, die auf denselben Slug abbilden (z. B. `a.b` und `a-b`) —
das ist eine Fail-closed-Prüfung, die die Config-Validierung beim Start
übernehmen muss (Konflikt ⇒ `ConfigError`, Warden startet nicht).

Begründung, warum **kein** `warden.toml`-Feld für Credentials:
- Grundsatz aus dem Plandokument (§8, „keine Geheimnisse in `warden.toml`") —
  Secrets bleiben strikt in der Umgebung/den Compose-Secrets, Policy strikt in
  `warden.toml`. Das ist dasselbe Prinzip, das schon `GITLAB_READ_TOKEN`
  (Env) von `allowed_projects` (`warden.toml`) trennt (CLI-Prinzip **P5**,
  `docs/design/agentic-workflow/04-cli.md`) — Multi-Host verändert dieses
  Prinzip nicht, es multipliziert nur die Menge der Env-Variablen.
- Die Host-**Liste** selbst ist keine Geheimnis, sondern Policy (welche
  Ziele der Agent überhaupt ansprechen darf) — sie gehört folgerichtig in
  `warden.toml`, exakt wie im Plandokument vorgegeben.
- Basis-URL pro Host wird **nicht** separat konfiguriert, sondern
  regelbasiert aus dem Hostnamen abgeleitet (`https://<host>/api/v4` für
  REST, `https://<host>` für git — dieselbe Ableitung, die
  `Config.git_base` heute aus `api_url` macht). Ein Host in der Allowlist
  bedeutet also implizit auch seine URL-Form; nur das Token ist zusätzlicher
  Input. Das hält die Konfiguration minimal (eine Liste + N Token-Paare statt
  einer Liste aus Tabellen mit URL+Tokens).

## 4. Aufgedeckte dritte Frage — State-Keying bei Multi-Host

Vom Plandokument nicht explizit adressiert, aber notwendig für Korrektheit:
Die Quota-/Reconcile-Zustände (`agent_branches`, `agent_mrs` — siehe
`guards/git/state.py::BranchState`, `guards/gitlab_api/state.py::MrState`)
sind heute **ausschließlich** nach Projektpfad (`group/proj`) geschlüsselt.
Sobald zwei Hosts erreichbar sind, können zwei *verschiedene* Repos
zufällig denselben Projektpfad tragen (`gitlab.com/acme/infra` und
`my-gitlab.de/acme/infra`) — mit reiner Projektpfad-Schlüsselung würden ihre
Branch-/MR-Zähler sich vermischen: ein Push auf Host A könnte fälschlich die
Quote von Host B verbrauchen oder ein R6-Deny auf Host B durch Host-A-Aktivität
auslösen. Das ist ein stiller Korrektheitsfehler, kein Abbruch — würde also
unbemerkt bleiben.

**Entscheidung:** Sobald mehr als ein Host konfiguriert ist, wird der
Zustand nach dem zusammengesetzten Schlüssel `(host, project)` geführt statt
nur `project`. Konkret:

- `agent_branches`/`agent_mrs` bekommen eine `host`-Spalte (Teil des
  Primärschlüssels/der Unique-Constraint zusammen mit dem Projektpfad).
- Reconcile (`reconcile_branches`, `reconcile_mrs`) läuft **pro Host** (eine
  Iteration über die Host-Allowlist statt eines einzelnen Laufs) und schreibt
  mit dem jeweiligen Host als Schlüsselteil.
- Für das bestehende Single-Host-Verhalten (leere/fehlende `[git.urls]`) ist
  das **kein sichtbarer Unterschied**: der implizite Host (aus `GITLAB_URL`
  abgeleitet) ist dann der einzige Schlüsselwert, faktisch identisch zum
  heutigen reinen Projektpfad-Schlüssel — bestehende DBs brauchen keine
  Migration, weil Punkt 2 des Backlogs (`state_migrations.py` entfernt) fail-
  closed über `PRAGMA user_version` geht: eine neue Spalte mit Default ist
  ein additiver Schema-Schnitt, der als neue `CURRENT_SCHEMA_VERSION`
  eingeführt würde, **wenn** dieser Teil tatsächlich implementiert wird (siehe
  Abschnitt 6 — in dieser PR nicht der Fall).

Das ist bewusst am Ende dieses Spikes vermerkt, weil es die Guard-
Parametrisierung (Abschnitt 2) direkt betrifft: `state_view()`/`reconcile()`
je Guard müssen um den Host-Dimension erweitert werden, sobald die
Host→Upstream-Abbildung (Abschnitt 2/3) real existiert — nicht vorher, sonst
entsteht eine Halbimplementierung, die bei mehr als einem Host falsche
Zähler liefert, ohne das sichtbar zu machen.

## 5. Nicht tun (bestätigt aus dem Plandokument, keine Änderung)

- Kein `insteadOf`-Pfad-Präfix-Trick.
- Kein separater Warden-Container pro Guard/Host auf Vorrat.
- Keine implizite/automatisch befüllte Host-Liste — explizite Allowlist in
  `warden.toml`, Default-Deny für alles Ungenannte.
- Keine Geheimnisse in `warden.toml` (Abschnitt 3).

## 6. Umsetzungsschnitt dieser PR — was jetzt Code wird, was offen bleibt

Der volle Umbau (Host→Upstream-Auflösung in beiden Guards, State-Keying nach
`(host, project)`, Reconcile pro Host, CLI-/Compose-seitige DNS-Aliase +
`git_routing.py`-Anpassung für mehrere kanonische Hosts, Container-Test mit
zwei echten Hosts) berührt die Trust-Boundary an mehreren Stellen gleichzeitig
(welcher Host bekommt welches Token, welcher Request landet bei welchem
Upstream) und reicht über das Warden-Python-Paket hinaus in die CLI-
Packaging-/Compose-Schicht (`src/catraz/...`), die in diesem Arbeitsschritt
nicht angefasst wurde und ohne Laufzeit-Verifikation (echte zweite
GitLab-Instanz, echtes Compose-Netzwerk) riskant blind zu ändern wäre. Das
entspricht der Einschätzung im Plandokument selbst („größter,
sicherheitssensitivster Schritt").

Diese PR liefert deshalb **nur** den sicher isolierten, additiven,
verhaltensneutralen Grundbaustein, vollständig durch Unit-/Config-Tests
abgesichert:

- `core/config.py`: `Config.allowed_hosts: frozenset[str]` (Default
  `frozenset()`) + `Config.host_allowed(host: str) -> bool`. Leere Allowlist
  ⇒ Verhalten wie heute (Methode gibt immer `True` zurück — es gibt noch
  keinen Aufrufer, der sie in den Request-Pfad verdrahtet, siehe unten).
  Nicht-leere Allowlist ⇒ strikter, Case-insensitiver, Port-strippender
  Vergleich, default-deny für alles andere.
- `core/config_load.py`: Parser für `[git.urls] hosts = [...]` aus
  `warden.toml` (Analog zu `[api.endpoints]`, fail-closed bei Falschform).
  Keine Env-Variable für die Liste selbst (Policy gehört in `warden.toml`,
  Abschnitt 3).

**Ausdrücklich NICHT Teil dieser PR** (Folgearbeit, separat zu planen):

1. Die Host→Upstream-Auflösung selbst (`GitGuard`/`ApiGuard` halten weiterhin
   genau **einen** `Upstream`, injiziert aus der weiterhin einzigen
   `Config.api_url`). `Config.host_allowed` wird **nirgends** in den
   Request-/Kernel-Pfad verdrahtet — ein halb angeschlossenes Gate wäre aktiv
   irreführend: es würde einen Host als „erlaubt" durchwinken, dessen Traffic
   dann trotzdem beim einzigen (falschen) Upstream landet.
2. Die per-Host-Credential-Auflösung (Abschnitt 3) — Slug-Ableitung,
   `GITLAB_READ_TOKEN__<SLUG>`-Parsing, Kollisionsprüfung.
3. State-Keying nach `(host, project)` (Abschnitt 4) — Schema-Änderung an
   `agent_branches`/`agent_mrs`, Reconcile pro Host.
4. CLI-/Compose-seitige DNS-Aliase, `git_routing.py`-Anpassung für mehrere
   kanonische Hosts, Rendering der Agent-Instruktionen für mehrere Remotes.
5. Der im Plandokument geforderte Container-Test (zwei erreichbare Hosts,
   ein dritter abgelehnt) — setzt 1–4 voraus.

**Fertig-Kriterium für Punkt 8 insgesamt bleibt offen**, bis 1–5 nachgezogen
sind; §07 Punkt 8 wird deshalb **nicht** als erledigt markiert. Dieser Spike
plus der Config-Grundbaustein sind der erste, sicher verifizierbare
Teil-Deliverable davon.
