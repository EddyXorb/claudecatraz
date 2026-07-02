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

### Runde 2

*(folgt nach dem zweiten Röst-Lauf)*
