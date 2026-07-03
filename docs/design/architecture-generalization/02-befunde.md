# 02 — Befunde im Bestand

Sicherheitsrelevante Befunde (B1–B4) und Code-Qualitäts-Findings (F1–F10) aus dem Lesen des
Warden-Codes, verifiziert in Röst-Runde 1. Jeder Befund ist unabhängig von der großen
Architekturlinie behebbar — B1 sollte **vor** allem anderen behoben werden.

## B. Sicherheitsbefunde

### B1 — Die Projekt-Allowlist greift auf dem Read-Pfad nicht überall **[HOCH]**

`_project_from_path` (`api_proxy.py:46`) extrahiert das Projekt nur aus Pfaden mit
`/projects/{id}`; `project_gate` lässt leere Projekte explizit passieren (`policy.py:47`,
„empty passes"). Damit laufen `GET /api/v4/projects` (alle für das Token sichtbaren Projekte
auflisten!), `GET /users/…`, `GET /groups/…` und die Such-Endpoints **ungescopt** mit dem
Read-Token durch (R1-Pass-through). Die tatsächliche Exfiltrations-/Lese-Grenze ist damit der
**Token-Scope**, nicht `allowed_projects` — im Widerspruch zur Doku („least privilege,
Read-Oberfläche klein", §6.10) und zu M6.

**Fix-Richtung (Maintainer-Entscheid nach Röst-Runde 2):** Die Schutzlinie ist **Inhalt,
nicht Sichtbarkeit**. Projekt- und Gruppennamen (Metadaten) dürfen gelesen werden — die
dokumentierte Discovery (`GET /groups/<id>/projects` in `AGENT.md`) bleibt uneingeschränkt.
Was `allowed_projects` auf dem Read-Pfad schützt, ist **Repository-Inhalt**: Files/Blobs/
Tree/Archive, Diffs, Wiki, Snippets, Commit-Inhalte. Daraus folgt eine eingebaute
Read-Tabelle mit vier Kategorien:

1. **Projekt-gebundene Pfade** (`/projects/{id}/…`) → Projekt-Gate wie heute. Deckt den
   Inhalt fremder Projekte bereits ab — `GET /projects/<fremd>/repository/files/…` ist
   schon heute R6-denied.
2. **Projektlose Metadaten-Endpoints** (`/projects`, `/groups/…`, `/users/…`, `/user`,
   `/version`) → pass. Namen, Pfade, Beschreibungen sind erlaubt.
3. **Projektlose inhaltsfähige Endpoints** → deny. Vor allem die globale und die
   Gruppen-Suche mit `scope=blobs|commits|wiki_blobs|notes` (liefert Code-Inhalte über
   *alle* sichtbaren Projekte!) sowie `/snippets`. Die projekt-gebundene Suchvariante
   (`/projects/{id}/search`) bleibt und läuft durch Kategorie 1. Achtung: `scope` ist ein
   Query-Parameter — F12 muss dafür mitgefixt sein (Query in die Entscheidung **und** ins
   Forwarding, konsistent).
4. **Unbekannt** → default-deny (A1); neue Metadaten-Endpoints werden bewusst freigeschaltet.

Damit entfällt die in Röst-Runde 2 erwogene Response-seitige Projekt-Filterung samt ihres
Buffering-Tradeoffs — Metadaten-Listen sind ja erlaubt, das Streaming-Prinzip bleibt
unangetastet. Als Defense-in-depth bleibt das **Read-Volumen-Budget** (M5-Erweiterung, §01)
sinnvoll: es macht auch Metadaten-Massen-Harvesting sichtbar. Das Ganze ist eine Invariante
an der Vertrauensgrenze im Sinn von A1/A2 und gehört als **Migrationsschritt 1** umgesetzt
(§06) — von Anfang an als (minimale) Read-Tabelle gebaut, die Schritt 4 nur erweitert,
nicht ersetzt.

### B2 — Verbotene Capabilities sind nicht kanalübergreifend **[HOCH, latent]**

Das Tag- und Branch-Delete-Verbot lebt ausschließlich in `check_ref` (`policy.py:107–115`),
also nur im git-Kanal. Die REST-Seite kennt diese Verbote nicht — heute harmlos, weil kein
Release-/Tag-Endpoint in der Write-Tabelle steht, aber **akut in dem Moment, wo Endpoints
konfigurierbar werden** (§04): `POST /projects/{id}/releases` mit geprüftem `tag_name`, aber
ungeprüftem `ref=main` erzeugt einen Tag auf `main` per REST. Verstoß gegen A3.

**Fix-Richtung:** Capability-Invarianten-Ebene (§03.4) — Intents werden auf ein kleines
Capability-Vokabular normalisiert (`creates_tag`, `deletes_ref`, `merges`, …), und eine
einzige einkompilierte `FORBIDDEN`-Menge wird vor jeder Allow-Regel geprüft, egal auf
welchem Kanal.

### B3 — Regel-IDs partitionieren die Regeln nicht sauber **[MITTEL]**

Tag-Push und Branch-Delete werden als **R2** geloggt (`policy.py:108,115`), obwohl sie
konzeptionell zu „irreversible Verben: niemals" (R4/M4) gehören; nur der Merge trägt R4.
Für ein Projekt, dessen Wert Auditierbarkeit ist, ist die Regel-Zuordnung im Audit-Log Teil
des Produkts. Die R-IDs sind zudem als bare Strings über fünf Module gestreut.

**Fix-Richtung:** zentrale Regel-Registry (Enum + Beschreibung + Meta-Regel-Zuordnung),
Guard-Namespacing vorbereitet (`gitlab.R4`). Achtung: Änderung der geloggten IDs ist eine
Audit-Schema-Änderung → braucht Schema-Versionierung (F11/§06 Schritt 2).

### B4 — `project_allowed` matcht Präfixe, Doktrin sagt exakt **[NIEDRIG]**

`config.py:89`: `project == allowed or project.startswith(allowed + "/")`. README/Design
doktrinieren „nur konkrete Projekte, keine Gruppen-Präfixe". Ehrlich eingeordnet (Röst-FC5):
Reconcile behandelt jeden Eintrag als konkretes Projekt und fail-closed bei Gruppen —
der Präfix-Zweig ist größtenteils toter, defensiver Code, kein akutes Loch. Trotzdem: nach
A8 exakt matchen (nach Normalisierung), toten Zweig entfernen, Test dazu.

### B5 — GraphQL ist nur zufällig zu **[NIEDRIG heute, HOCH sobald geroutet]**

Der Agent-Port routet ausschließlich `/api/v4/{rest}` und die drei git-Routen (`app.py:22–30`).
GitLabs GraphQL-API liegt unter `/api/graphql` — sie ist heute schlicht nicht erreichbar,
also *zufällig* sicher, nicht *entworfen* sicher. Eine einzige GraphQL-Mutation kann alles,
was der REST-Write-Filter verbietet (Tag erzeugen, MR mergen). Sollte je jemand GraphQL
„für bessere Reads" durchrouten, fällt die gesamte Write-Policy.

**Fix-Richtung:** GraphQL als explizites Anti-Ziel dokumentieren (§06.2) und im Warden
aktiv mit 403 + Audit beantworten (statt 404), damit die Absicht im Code steht. Ein
GraphQL-Durchlass wäre nur als eigener Guard mit eigener Capability-Ableitung zulässig (A3).

## F. Code-Findings (verhaltenserhaltend behebbar)

1. **Pipeline zweimal von Hand** — `api_proxy.handle` (`api_proxy.py:76–125`) und
   `git_proxy.receive_pack` (`git_proxy.py:111–189`) bauen Deny-Kurzschluss,
   record-before-forward und Audit jeweils selbst. → Kernel-Extraktion (§03.2), erzwingt A5.
2. **Kopplung über Funktions-Identität** — `api_proxy.py:102` prüft
   `mr_owned_by_claude in ep.checks`. → Checks deklarieren `needs`, Kernel enriched (§04.1).
3. **`ProxyRequest` als Kanal-Union** (`model.py:51–63`) — trägt git- (`ref_commands`) und
   API-Felder (`path`/`fields`/`mr_owner_ok`) gleichzeitig. → Intent-Typen pro Guard (§03.3).
4. **`Config` mischt statische Policy und Laufzeit-Cache** — `allowed_project_ids`
   (`config.py:51`) wird von `context.reconcile` per `replace(self.cfg, …)` zur Laufzeit
   getauscht (`context.py:121`). → statische `Policy` von aufgelöstem Zustand trennen.
5. **Regel-IDs als Streuliteral** — siehe B3.
6. **Audit-Schema doppelt** — `git_proxy._audit` und `api_proxy._audit` bauen fast identische
   Dicts. → ein `AuditEvent`-Datentyp im Kern; Feld `guard` statt `channel` (nur zusammen mit
   Schema-Versionierung, F11).
7. **Viewer-HTML inline in `app.py`** (~90 Zeilen String) → statisches Package-Asset.
8. **`EndpointKind` verdrahtet Quoten-Dimensionen** — Quoten-Dimensionen (`mr.open`,
   `branch.open`, `writes.hour`) gehören als benannte Zähler in den Kern (M5); Guards mappen
   ihre Kinds darauf. Dann braucht ein neuer Guard keine Kern-Änderung für eigene Quoten.
9. **`Upstream` ist GitLab-spezifisch** (`PRIVATE-TOKEN`, `oauth2:`-Basic, `/api/v4`-Annahme,
   `upstream.py`) → generischer Streaming-Client + `CredentialAdapter` pro Guard (§03.3).
10. **`src_branch_prefix` und `ref_prefix` sind dieselbe Funktion** mit anderem Feldnamen
    (`api_endpoints.py:42–53`) → ein parametrisierter Registry-Check `field_has_prefix`.
11. **(neu, aus Röst-R5) Persistenz und Audit sind unversioniert** — SQLite-Tabellen heißen
    `claude_branches`/`claude_mrs`, der Viewer und `catraz observe` lesen `channel`/`kind`
    aus dem JSONL. Jeder Rename (claude→agent, channel→guard) ist eine Schema-Migration mit
    Kompatibilitätsfenster, kein Suchen-und-Ersetzen. → expliziter Versionierungs-Schritt
    vor allen Renames (§06 Schritt 2).
12. **(neu, aus Röst-Runde 2) Query-Parameter fließen in die Entscheidung ein, werden aber
    nicht weitergeleitet** — `api_proxy.py:42` schneidet den Querystring vom Pfad ab, doch
    `_extract_fields` (`api_proxy.py:60`) nimmt `request.query_params` in die
    Entscheidungsfelder auf; `open_rest` forwarded den querylosen Pfad. Heute zufällig
    fail-closed (der Wert erreicht GitLab nie), aber ein Footgun für konfigurierbare
    Endpoints: ein Scoping-Check könnte auf einem Query-Feld „passen", das der Upstream nie
    sieht. → Katalog-Einträge (§04) deklarieren pro Feld, ob es in Body oder Query lebt, und
    das Forwarding muss konsistent sein: entweder Query mitschicken oder Query-Felder aus
    der Entscheidung ausschließen.
