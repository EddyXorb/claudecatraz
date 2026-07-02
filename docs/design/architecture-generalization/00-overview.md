# Architektur-Generalisierung — Übersicht

**Status: Ideensammlung / Diskussionsgrundlage — nichts hiervon ist implementiert.**

Anlass: Die aktuelle Architektur ist auf GitLab und Claude spezialisiert. Vier konkrete
Schmerzpunkte:

1. **GitLab-Kopplung** — Warden, Endpoint-Tabelle, Upstream und Reconcile sprechen nur GitLab.
2. **Fix verdrahtete Endpoints** — die erlaubten Write-Endpoints leben als Tupel in
   `warden/warden/api_endpoints.py`; von außen (Config) lässt sich nichts hinzufügen.
3. **Keine klare Trennung git vs. GitLab-API** — `git_proxy` und `api_proxy` teilen implizit
   eine Pipeline, aber keine Abstraktion trägt sie; ein späterer Datenbank-Guard hätte keinen
   Andockpunkt.
4. **Claude als fester letzter Layer** — der Agent-Layer ist der einzige, den `catraz` kennt;
   andere Modelle/CLIs sind nicht vorgesehen.

Leitplanke für alles: das Projektziel **Sicherheit bei Anwenderfreundlichkeit**. Jede
Generalisierung ist nur dann eine Verbesserung, wenn sie das Sicherheitsmodell strukturell
erhält — nicht per Konvention, sondern so, dass man es gar nicht verletzen *kann*.

Zweite Leitplanke (Maintainer-Entscheid): **Clean Code steht ganz oben.** Single
Responsibility, keine Duplikation, kleine Funktionen und Module, die je genau einer
Abstraktionsschicht zugeordnet sind. Saubere Struktur ist kein Selbstzweck, sondern die
Voraussetzung dafür, die richtigen Generalisierungslinien überhaupt zu *sehen* —
Refactorings dürfen und sollen den Feature-Schritten vorausgehen.

## Dokumentkarte

| Dokument | Inhalt |
| -------- | ------ |
| [`01-grundregeln.md`](./01-grundregeln.md) | Die Axiome A1–A11 und das Meta-Regelwerk (harter Kern vs. plattformabhängig) |
| [`02-befunde.md`](./02-befunde.md) | Sicherheitsbefunde im Bestand (inkl. Read-Pfad-Lücke) + Code-Findings |
| [`03-guard-architektur.md`](./03-guard-architektur.md) | Guard-Abstraktion, Kernel-Pipeline, Capability-Invarianten, Forge- und DB-Guard |
| [`04-policy-erweiterbarkeit.md`](./04-policy-erweiterbarkeit.md) | Konfigurierbare Endpoints: Check-Registry, TOML, Policy-by-Example |
| [`05-agent-layer.md`](./05-agent-layer.md) | Austauschbarer Agent-Layer: Entrypoint-Zerlegung, Profile, Egress |
| [`06-migration.md`](./06-migration.md) | Migrationspfad + Anti-Ziele |

## Röst-Protokoll

Die Ideen wurden in adversarialen Runden geröstet (Reviewer-Subagent mit Code-Zugriff);
Annahmen/Ablehnungen sind hier festgehalten, damit die Begründungen nicht verloren gehen.

### Runde 1 — angenommen

- **Read-Pfad-Lücke** (Röstung R1, verifiziert): die Projekt-Allowlist greift auf dem
  Read-Pfad nur, wenn `/projects/{id}` im Pfad steht; `GET /projects`, `/users/…`,
  `/groups/…`, Suche laufen ungescopt mit dem Read-Token durch. → neuer Befund B1,
  Migrationsschritt 1.
- **TOML-Endpoints allein sind unsicher** (R2): ein Scoping-Check auf dem falschen Feld
  (Release: `tag_name` geprüft, `ref=main` ungeprüft) erzeugt per REST einen Tag auf `main` —
  das Tag-Verbot lebt heute nur im git-Pfad. → Capability-Invarianten-Ebene als
  Voraussetzung für jede Endpoint-Konfigurierbarkeit (Roaster-Idee 1, jetzt Kern von §04).
- **M3 ist keine Meta-Regel** (R3): „nur eigene Objekte" setzt serverseitiges
  Autorschafts-Tracking voraus (Forges: ja, Postgres: nein). → M-Tabelle zweigeteilt in
  harten Kern und plattformabhängige Regeln.
- **Regel-ID-Inkonsistenz** (Faktencheck FC6): Tag-Push/Branch-Delete werden im Code als R2
  geloggt, nicht R4. → Befund B3, Regel-Registry.
- **Rename ist nicht risikolos** (R5): SQLite-Tabellen `claude_branches`/`claude_mrs`,
  Audit-Feld `channel` und der Viewer hängen dran. → Schema-Versionierung als eigener
  Migrationsschritt vor jedem Rename.
- **Agent-Profile lösten die einfachen 20 %** (R7): die echte Claude-Kopplung ist der
  ~400-Zeilen-Entrypoint und der Credential-Refresh, nicht das Manifest; Egress-Domains
  dürfen nie automatisch gemergt werden. → §05 neu geschnitten.
- **Axiome gestrafft** (R8): A2+A3 zusammengelegt (Invarianten-Regel ist Korollar), die
  Pipeline von Axiom zu Implementierung abgerüstet; zwei fehlende Axiome ergänzt
  (fail-closed bei Ungewissheit; Invarianten sind kanalübergreifend).
- **Policy-by-Example** (Roaster-Idee 3) als ergänzende Nutzeroberfläche übernommen.
- **Kurzlebige, eng gescopte Tokens** (Roaster-Idee 4) als self-hosted-Richtung übernommen.
- **Befund `project_allowed`-Präfix-Match ehrlich herabgestuft** (FC5): Reconcile macht
  Gruppen-Einträge ohnehin fail-closed; der Präfix-Zweig ist größtenteils toter Code —
  entfernen ja, aber kein akutes Loch.

### Runde 1 — abgelehnt / modifiziert

- **„TOML-Endpoints als ersten Schritt vorziehen" (R6): abgelehnt.** Widerspricht direkt
  Röstung R2 desselben Reviews — TOML ohne Capability-Invarianten wäre die Auslieferung der
  Lücke als Feature. Übernommen wurde nur der berechtigte Kern: die Kernel-Extraktion darf
  den Nutzerwert nicht blockieren (sie steht jetzt *hinter* den Sicherheitsschritten, nicht
  davor).
- **„Prozess pro Guard" (Roaster-Idee 2): modifiziert.** Berechtigt als Konsequenz aus A6,
  aber vor dem zweiten credential-haltenden Guard reine Betriebskomplexität ohne Gewinn
  (das räumt der Review selbst ein). Festgehalten als verbindlicher Plan *ab* dem zweiten
  Guard, bis dahin als dokumentierte Grenze des Monoliths.

### Runde 2 — angenommen

- **Nutzerdeklariertes `capabilities`-Feld gekippt** (R2-Röstung 1+2, Faktencheck A): §03.4
  („Capability-Abbildung nie aus Nutzer-Config") und §04.2 (Pflichtfeld in Nutzer-TOML)
  widersprachen sich; für genuin neue Endpoints wäre die Nutzer-Deklaration die einzige
  Schutzschicht gewesen — und der Normalfall ist Unwissen, nicht Bosheit. → **Katalog-Ansatz**:
  Code liefert einen geprüften Endpoint-Katalog (Capabilities, Checks, Deny-Sonden), Config
  aktiviert per ID und darf nur verengen. Sicherer *und* anwenderfreundlicher; §04 neu
  geschrieben, Anti-Ziel „keine freien Endpoint-Zeilen" ergänzt.
- **B1-Fix war workflow-blind** (Faktencheck B, Röstung 4): `AGENT.md` verlangt
  `GET /groups/<id>/projects` (projektloser Read); ein naives default-deny bricht die
  dokumentierte Discovery. → zweiteiliger Fix: kategorisierte Read-Tabelle +
  **Response-seitige Projekt-Filterung** der Listen-Endpoints (Roaster-Idee I1), plus
  Read-Volumen-Budget als M5-Erweiterung (Idee I2).
- **GraphQL unmodelliert** (Röstung 5): `/api/graphql` ist heute nur zufällig nicht geroutet;
  eine Mutation könnte die gesamte Write-Policy umgehen. → Befund B5, aktives 403 + Audit,
  Anti-Ziel; A3 ehrlich auf Wirkungen (Write-artiges) eingegrenzt.
- **Query-Param-Inkonsistenz** (Faktencheck C): Entscheidung liest Query-Felder, Forward
  verwirft den Querystring — heute zufällig fail-closed, Footgun für den Katalog. → Befund
  F12; Katalog-Einträge deklarieren Feld-Lage (Body/Query).
- **Mode-Gate-Reihenfolge unterspezifiziert** (Faktencheck D): `read-only` braucht die
  Write-Klassifikation; „M0 zuerst" gilt strikt nur für `off`. → §03.2 präzisiert:
  `intent.writes` kommt vom Parser, das read-only-Gate läuft nach `parse`, vor jeder
  Allow-Logik.
- **Kernel-Regel-Namespace** (Röstung 7): Capability-/Mode-/Allowlist-Denials sind nicht
  guard-spezifisch. → reservierter Namespace `core.*` in der Regel-Registry (A7, Schritt 2).
- **Adapter-Vertrag unvollständig** (Röstung 6): Env-Injektion und der Inhalt (nicht nur
  Ort) der Instruktionsdatei fehlten; die heutige `AGENT.md` ist nicht agent-neutral. →
  `environ()` + `render_instructions()` im Protokoll, Forge-REST-Basis als expliziter Input.
- **Migrations-Ehrlichkeit** (Röstung 8): Startgate/Deny-Sonden gehören in Schritt 4 (nicht
  erst 8), Schritt 1 als minimale Read-Tabelle ohne Rework, Prozess-Trennung als eigener
  Schritt 10 statt Beifang von 9.
- **Adapter-Conformance-Harness** (Roaster-Idee I3) übernommen (§05.5): macht A11 pro
  Adapter/Fork zu einem rot/grün-Signal.
- **Policy-by-Example ehrlich herabgestuft** (Röstung 3): UX-Zucker über dem Katalog, kein
  Sicherheitsmechanismus für unbekannte Endpoints; Deny-Sonden leben beim Katalog-Eintrag
  (ein Korpus, ein Eigentümer).

### Runde 2 — abgelehnt / modifiziert

- **„A3 auf ‚alle Write-Kanäle' einschränken" (Röstung 5, Teilvorschlag): modifiziert statt
  übernommen.** A3 bleibt als Prinzip über *Wirkungen* (Capabilities) formuliert — die
  bessere Einschränkung ist nicht „nur Write-Kanäle", sondern: unmodellierte Kanäle bleiben
  komplett zu (GraphQL-403), und Reads werden von M1/M6 + Response-Scoping regiert. So
  bleibt das Axiom scharf, ohne Unerfülltes zu versprechen.
- **Kein Experten-Escape-Hatch für freie Endpoint-Zeilen ergänzt** (naheliegende
  Abschwächung des Katalog-Ansatzes, die der Review nicht forderte, aber Nutzer fordern
  werden): bewusst nicht aufgenommen. Jede „ich weiß, was ich tue"-Variante stellt genau
  die Vertrauens-Verlagerung wieder her, die Runde 2 als Kernproblem identifiziert hat;
  der Katalog-PR ist klein genug, um der einzige Weg zu bleiben.

### Maintainer-Entscheidungen (nach den Röst-Runden)

- **Read-Scoping: Inhalt, nicht Sichtbarkeit.** Reads werden durchgesetzt, aber
  Projekt-/Gruppennamen und Metadaten dürfen gelesen werden — geschützt ist
  **Repository-Inhalt** (Code, Diffs, Wiki, Snippets) nicht explizit erlaubter Projekte.
  Damit entfällt die in Runde 2 erwogene Response-seitige Projekt-Filterung samt
  Buffering-Tradeoff; stattdessen kategorisierte Read-Tabelle mit Deny der projektlosen
  inhaltsfähigen Endpoints (v.a. globale Blob-/Commit-Suche). Details: Befund B1,
  Migrationsschritt 1.
- **Branch-Namensraum als Liste.** `branch_prefix` wird zu `branch_prefixes` — eine Liste
  erlaubter Präfixe (ein Eintrag = heutiges Verhalten; leer bleibt verboten). Der
  M2-Namensraum ist die Vereinigung der Präfixe; alle R2/R3-Checks und der Reconcile
  prüfen gegen die Liste.
