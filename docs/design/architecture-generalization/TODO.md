# TODO

## Warden codebase ist Durcheinander

- api-endpoints in top level module makes no sense; should be deleted and api guard takes over
- the core guard should define a Route-generator der -> list[Route] zurückgibt, so dass in app() nur diese listen von den guards erstellt werden. Das ist zugleich der einstiegspunkt für jeden guard, da somit die routen mit den handlern verknüpft werden. Falls es sein muss, dass die handler freie funktionen sein müssen, sollten sie jedenfalls 
- run_guarded sollte ein member der Guard-core classe werden (falls das mit Protocol geht) und dann sollte ALLES darüber laufen. der git-guard schleift nur pushes darüber und den rest über freie funktionen - warum???
- es ist sehr unglücklich, dass das AppContext im gitlab_api-namespace lebt. Das ist eigentlich komplett der falsch ort. richtig wäre, dass der appcontext im root level lebt und die nötigen states der guards im konstruktor der guards eingepflanzt kriegen (vom konstruktor des jeweiligen guards selbst), damit außerhalb der guards keine guard-fremde logik mehr ist. Er enthält auch logik, was auch falsch ist. diese sollte in den guards liegen
- die docstrings sind oft wall of texts ohne viel inhalt. das muss konziser sein. außerdem sollen sie sich nicht mehr auf die docs beziehen sondern der code soll selbstgenügend sein (erklärungen dürfen schon sein, aber nur im code selbst als docstring wenn nötig, aber konzise)
- man sieht das zielbild der toml-datei nicht gut wenn man nur den code liest. An einer passenden stelle im code sollte in einem kommentar ein vollständiges beispiel stehen für ein toml-file
- der state im core ist auch unglücklich, weil details aus dem gitlab-api guard da reinkriechen. Stattdessen sollte jeder guard einen eigenen state kriegen wenn er ihr brauch (könnte z.b. eigene datenbank sein in der selben sql-datei) und der guard versteckt dann die state-logik seiner datenbank in seinem module. Das wird mit basisklassen abstraktionen angedeutet dass ein guard das machen kann und die einzige interaktion mit dem core-modul ist dann, dass das core-modul sich um das anlegen und generelle verwalten der datenbank kümmert und die tools liefert um dinge zu persistieren für die guards
-

## Bewertung der Einwände (2026-07-03)

1. **`api_endpoints.py` löschen — berechtigt.** Das Modul ist eine reine Kompat-Fassade
   aus Schritt 4/5; ein Grep über `warden/` und `tests/` findet **keinen einzigen
   Importer** mehr. Totes Modul, ersatzlos streichbar.
2. **Route-Generator pro Guard — berechtigt.** `app.py` importiert heute Guard-Interna
   (`git_guard.advertise`, `api_guard.handle`, `api_guard.deny_graphql`) und verdrahtet
   sie von Hand. Ein `routes() -> list[Route]` am Guard macht den Guard zum einzigen
   Einstiegspunkt und `create_app` zu generischer Assemblierung. Der GraphQL-Deny gehört
   dabei zum gitlab_api-Guard, nicht in `app.py`. (Der abgebrochene Satz „falls die
   handler freie funktionen sein müssen…" erledigt sich: sie müssen nicht — Starlette
   akzeptiert gebundene Methoden als Endpoint.)
3. **`run_guarded` als Member + ALLES darüber — berechtigt.** Der „dünne Handler"-
   Carve-out (§03.2) produziert real: dreimal handgebaute Gate-Sequenzen
   (`advertise`, `upload_pack`, `deny_graphql`) und eine Audit-Inkonsistenz — der
   API-Guard auditiert jeden Request inkl. Reads, der git-Guard auditiert Reads
   **gar nicht** (A7 verletzt). Reads sind im Pipeline-Modell schlicht Intents mit
   `writes=False`; der Carve-out hat keinen tragenden Grund mehr. Technisch: `Protocol`
   → ABC wandeln (beide Guards subclassen ohnehin schon explizit `Guard[...]`), dann
   ist `run_guarded` als Template-Methode natürlich.
4. **`AppContext` im falschen Namespace + enthält Logik — berechtigt.** Die Docstrings
   in `context.py` und `git/guard.py` geben es selbst zu („honest scope note"). Der
   git-Guard importiert heute aus `gitlab_api` (Schichtverletzung), `__main__.py` und
   `app.py` hängen an einem Guard-Paket. Zusätzlich: Reconcile-, Ownership- und
   Service-Account-*Logik* lebt im Context statt im Guard.
5. **Docstrings — berechtigt.** `core/guard.py` allein trägt 13 §/F/W/Schritt-Referenzen,
   `context.py` 8. Viel davon ist Migrations-Erzählung („pre-Schritt-5",
   „F1's actual complaint") — wertlos ohne die Design-Docs und nach Abschluss der
   Migration reine Ablenkung.
6. **TOML-Beispiel im Code — berechtigt.** `config_load.py` beschreibt die Semantik
   einzelner Felder, aber nirgends im Code steht ein vollständiges `warden.toml`.
7. **State im Core — berechtigt, mit einer Präzisierung.** `writes` + `meta`/Locking
   sind echt generisch und bleiben Core. `agent_branches`/`agent_mrs` sind
   Forge-Vokabular im Core — der Docstring behauptet Ressourcen-Agnostik, die Tabellen
   widerlegen ihn. Aber: diese Tabellen gehören nicht *einem* Guard — der git-Guard
   schreibt Branches, der API-Guard MRs, beide Policies lesen beide Zähler. Die
   richtige Einheit ist die geteilte Forge-Domäne (§03.5), nicht „ein Guard = eine
   Tabelle". Das Zielbild „Core liefert DB-Verwaltung + Persistenz-Werkzeug, Domäne
   versteckt ihr Schema" trägt trotzdem.

## Plan: Architektur-Ausbesserung

Reihenfolge nach Abhängigkeit; jeder Schritt einzeln shipbar, bestehende Tests als
Verhaltensnetz. A und F sind frei; B → C → D; E braucht D (Guard-Basisklasse als
Aufhänger für Guard-State); G (Docstrings) zuletzt, weil vorher noch Code bewegt wird.

### A. `api_endpoints.py` ersatzlos löschen

Keine Importer, keine Ersatz-Fassade. Fertig-Kriterium: Modul weg, Testlauf grün.

### B. `Guard`: Protocol → ABC mit `run_guarded` als Template-Methode

- `core/guard.py`: `class Guard(ABC, Generic[IntentT])`; die Hooks (`parse`, `enrich`,
  `capability_gate`, `decide`, `record`, `forward`, `deny_response`, `audit_fields`)
  bleiben abstrakt; `run_guarded` wird finale Methode `Guard.handle(request)`.
- Die Kollaborateure, die die Pipeline braucht (`cfg`, `state`, `audit`), nimmt der
  ABC-Konstruktor entgegen — damit entfällt das explizite Durchreichen an jeder
  Aufrufstelle und Schritt D hat schon die richtige Signatur.
- `GitGuard`/`ApiGuard` erben statt strukturell zu matchen; das freie `run_guarded`
  verschwindet.

### C. Alles durch die Pipeline: Read-Intents statt dünner Handler

- git: `advertise` und `upload_pack` werden Pipeline-Läufe mit einem Read-Intent
  (`writes=False`, Projekt aus dem Pfad, Decision = allow/READ-Token als pures
  `decide`). Die handgerollten `mode_gate_off`/`project_gate`-Sequenzen entfallen;
  der Sonderfall „Push-Discovery zählt als Write" (`?service=git-receive-pack`)
  wandert in `parse` (setzt `writes=True`).
- `deny_graphql` wird ein Pipeline-Lauf mit einem Intent, dessen `decide`
  unconditional-deny ist — der handkopierte Audit-Block in `gitlab_api/guard.py`
  entfällt.
- Verhaltensänderung (gewollt, dokumentieren): git-Reads erscheinen ab jetzt im
  Audit-Log wie API-Reads. Falls Log-Volumen ein Problem wird, ist das ein
  Audit-Filter-Feature, kein Grund, Reads an der Pipeline vorbeizuschleusen.
- Fertig-Kriterium: kein Handler außerhalb von `Guard.handle`; `mode_gate_*`/
  `project_gate` haben genau einen Aufrufer (`kernel_gates`).

### D. `AppContext` zerlegen: Root-Kontext + Guard-eigener State

- Neues `warden/context.py` (Root-Level): hält nur die generischen Kollaborateure
  (`cfg`, `state`, `audit`) plus die konstruierten Guard-Instanzen. Keine Logik.
- `Upstream`, Service-Account-Resolution, Owner-Cache und Reconcile ziehen in die
  Forge-Domäne: ein `guards/gitlab/`-Paket (Arbeitsname) mit `forge.py`
  (Upstream + Service-Account + Reconcile) — beide Guards (git, api) bekommen die
  Forge-Instanz im **eigenen Konstruktor** injiziert; damit importiert der git-Guard
  nichts mehr aus `gitlab_api`, sondern beide aus der gemeinsamen Forge-Schicht.
- Reconcile/Startup wird formaler Guard-Lifecycle: `Guard.startup()`- und
  `Guard.reconcile()`-Hooks (Default: no-op) am ABC; `__main__.py` iteriert über
  Guards statt `ctx.reconcile()` zu kennen. Der Periodic-Reconcile-Loop bleibt in
  `__main__`, ruft aber nur noch die Hooks.
- `app.py`/`__main__.py` importieren danach kein Guard-Paket-Internum mehr:
  `create_app(guards, ctx)` sammelt `guard.routes()` ein (aus B/C), fertig.
- Offene Folge-Baustelle (nicht dieser Schritt): `Config.effective_endpoints` greift
  per Deferred-Import in den Katalog des gitlab_api-Guards — dieselbe Krankheit wie
  AppContext, umgekehrte Richtung. Kandidat: Aktivierungs-Parsing als Guard-Hook
  (`Guard.validate_config(cfg)`), Katalog-Zeug komplett raus aus `core/config.py`.

### E. Persistenz: Core verwaltet die DB, Domänen besitzen ihre Tabellen

- `core/state.py` schrumpft auf das Generische: Connection-Aufbau (WAL,
  `synchronous=FULL`), Migration-Runner, `meta`/Reconcile-Lock, `writes`-Zähler
  (`record_write`/`writes_last_hour`/`prune`) und ein schmales Persistenz-Werkzeug
  (`StateStore`: Connection-Handle + Namespace-Konvention + Migrations-Registrierung
  pro Domäne).
- `agent_branches`/`agent_mrs` samt `add_branch`/`upsert_mr`/`replace_*`/
  `open_branches`/`open_mrs` ziehen als `ForgeState` in die Forge-Domäne aus D
  (gleiche SQLite-Datei, eigene Tabellen, Schema-Migrationen registriert die Domäne
  beim Core-Runner). `StateView` bleibt Core (pures Snapshot-Value-Objekt), wird aber
  von der Domäne befüllt.
- Fertig-Kriterium: `core/` enthält kein Wort GitLab/MR/Branch mehr; ein Guard ohne
  State braucht keinen; ein neuer Guard bekommt State ausschließlich über `StateStore`.

### F. Vollständiges `warden.toml`-Beispiel in den Code

- Ein komplettes, kommentiertes Beispiel (alle Sektionen: Modus, Projekte, Limits,
  `[api.endpoints]`, Admin/Ports, Pfade) als Modul-Docstring-Block in
  `core/config_load.py` — dort, wo geparst wird. Muss beim Lesen des Parsers das
  Zielbild komplett zeigen.
- Optional zusätzlich als `warden.toml.example` ins Paket, aber die kanonische Kopie
  lebt im Code; ein Test parst das Beispiel durch `from_env`, damit es nicht driftet.

### G. Docstring-Pass: konzise und selbstgenügend

- Alle §/F/W/Schritt-/„Röst-Runde"-Referenzen und Migrations-Erzählung
  („pre-Schritt-5", „replaces the manual guard…") raus; was eine echte Invariante
  beschreibt (z. B. „record vor forward, damit ein Crash nie einen Write verliert"),
  bleibt — in einem Satz, aus sich heraus verständlich.
- Faustregel: Docstring erklärt *was gilt und warum*, nie *was vorher war* oder
  *wo es dokumentiert ist*. Zielgröße: Modul-Docstrings ≤ 5 Zeilen, Methoden ≤ 3.
- Zuletzt ausführen (nach A–E), damit der Pass nicht Text poliert, der ohnehin
  verschoben oder gelöscht wird.
