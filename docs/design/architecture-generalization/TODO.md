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

## Stand der Umsetzung (2026-07-03, Branch claude/migration-steps-2-8-2e4aas)

Erledigt und einzeln committet:

- ✅ **A** — `api_endpoints.py` gelöscht (Commit `b4d97ba`).
- ✅ **B** — Guard-Protocol → ABC, `run_guarded` ist jetzt Template-Methode
  `Guard.handle`; Guards kriegen `cfg`/`state`/`audit` im ABC-Konstruktor
  (Commit `1d1a251`).
- ✅ **C** — alles läuft durch die Pipeline: `GitReadGuard` (advertise/upload-pack,
  Push-Discovery setzt `writes=True`), `GraphqlGuard` (unconditional deny).
  git-Reads werden jetzt auditiert; Deny-Reihenfolge überall off → writes → project
  (R0 statt R6 in zwei Randfällen, in Tests dokumentiert). 344 Tests grün
  (Commit `c194d46`).

Erledigt (Fortsetzung):

- ✅ **D** — AppContext zerlegt. D1 (`85730da`): git-Guards zu einem
  `GitGuard`/`GitIntent` verschmolzen. D2 (`6611817`): Forge-Schicht
  `guards/gitlab/` (`GitlabForge` + `upstream.py`, beiden Guards injiziert),
  logikfreies Root-`warden/context.py` (`build_context`), Guard-Lifecycle
  (`startup`/`reconcile`), `routes()` pro Guard, `create_app(ctx)` generisch;
  Config-Mutation beendet (`allowed_project_ids` raus aus `Config`,
  `project_id_aliases` als Forge-State, `project_allowed` als Guard-Hook).
- ✅ **E** — Persistenz gesplittet (`bc25505`): `StateStore` (Connection +
  Migrations + writes-Zähler/Lock) im Core, `agent_branches`/`agent_mrs` als
  `ForgeState` in `guards/gitlab/state.py`; `Guard.state_view()`-Hook.
- ✅ **H** — Entfettet (`23a1b61`): Override-Mechanismus gelöscht, Deny-Probes
  nach `catalog/probes.py` ausgelagert, `git_reject_response` in `guards/git/errors.py`.

- ✅ **F** — Dataclass-Config + generischer TOML-Decoder (`core/toml_codec.py`).
  Deferred-Import-Trick (`Config.effective_endpoints` → Katalog) aus
  `core/config.py` entfernt (`core/` importiert nichts mehr aus `guards/`),
  `catalog/config_parse.py` durch den Decoder ersetzt (`ApiEndpointsConfig`),
  effektive Tabelle vom `ApiGuard` (`self._effective`) besessen und durch
  Policy/Startgate gefädelt statt via `cfg.__dict__`-Hack.

Offen (Reihenfolge: I):

- ✅ **G** — Docstring-Pass. Drei Haiku-Subagenten mit disjunkten Datei-Scopes
  (core/+top-level, git/+gitlab/, gitlab_api/+catalog/), parallel gelaufen.
  34 Dateien geändert, ~190 Zeilen netto entfernt (§/Schritt/Röst-Runde-Zitate
  und Migrations-Erzählung raus, Invarianten und Rule-Id-Tags behalten).
  348 Tests grün, ruff/mypy clean.
- ✅ **H2** — Startgate abbauen. Ein Sonnet-Subagent: `startgate.py`,
  `probes.py`, `DenyProbe`, `BUILTIN_DENY_PROBES`, `PROBE_PROJECT`/
  `OTHER_PROJECT`, `StartgateFailure` gelöscht; `is_builtin_merge_endpoint`
  blieb (Laufzeit-Policy). Config-Validierung läuft weiter fail-closed über
  `ApiGuard.__init__` → `build_effective_table` (`__main__.py`s separater
  Pre-Check war redundant dazu). Drei entry-spezifische Deny-Probe-Szenarien
  ohne bestehende Testabdeckung (mr.discussion_reply-Ownership, mr.update-
  Ownership, R6-Projektgrenze bei `checks=()`) als normale pytest-Fälle in
  `test_policy.py`/`test_api_proxy.py` migriert; der Rest war bereits
  redundant zu bestehenden Tests. 344 Tests grün (348 − 6 gelöschter
  Startgate-Tests − 1 gelöschter Startgate-Failure-Test + 3 neue), ruff/mypy
  clean.
- ⬜ **I** — Katalog auf `Recognizer → ⟨Capability, Scope⟩` vereinheitlichen
  (neu, siehe unten). Reads und Writes fallen unter *ein* Modell.

Arbeitsmodus bisher: pro Schritt ein Sonnet-Subagent (Prompt aus dem Plan-Abschnitt
unten plus betroffene Dateien plus Verifikation: `cd warden && uv run pytest -q`,
`ruff check .`, `mypy warden`), danach Diff-Review und einzelner Commit. Working
Tree ist sauber, alle Commits auf dem Branch.

## Plan: Architektur-Ausbesserung

Reihenfolge nach Abhängigkeit; jeder Schritt einzeln shipbar, bestehende Tests als
Verhaltensnetz. A ist frei; B → C → D; E braucht D (Guard-Basisklasse als Aufhänger
für Guard-State); H (Entfetten) nach C jederzeit; F (Dataclass-Config) braucht H
(Overrides weg ⇒ Schema statisch typbar) und profitiert von D (Guard-eigene
Config-Sektion); G (Docstrings) zuletzt, weil vorher noch Code bewegt wird.
Ausführungsreihenfolge: A, B, C, D, E, H, F, G, **H2, I** (die letzten zwei
sind die 2026-07-03 entschiedenen Vereinfachungs-Schritte: es ist insgesamt zu
viel Ballast im System — siehe unten).

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

- GitPushIntent/GitReadIntent vereinheitlichen zu einem Intent und
   damit auch aus zwei Guards einenmachen.
   Grundregel: immer nur einen guard je namespace (git, gitlab,...)
   der alles übernimmt. Zum dispatchen kann er member helper haben,
   aber es sollte gehen alles durch den üblichen flow zu schicken mit "handle";
   wenn das nicht klappt muss handle noch mehr generalisiert werden, aber es MUSS durch ihn alles gehen
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
- **Harte Anforderung — Config-Mutation beenden:** `reconcile()` macht heute
  `self.cfg = replace(cfg, allowed_project_ids=…)` — zur Laufzeit aufgelöste
  numerische Projekt-Ids sind *State*, kein Config. Sobald Guards langlebig sind
  (statt pro Request konstruiert), wäre das im Konstruktor gebundene `self.cfg`
  stale und die Id-Allowlist leer. Fix: die Id-Aliase wandern in den Forge-State
  (z. B. `ForgeState.project_id_aliases`), `project_gate` fragt dafür eine vom
  Guard gelieferte Auflösung; `Config` bleibt ab Konstruktion wirklich eingefroren.
- Offene Folge-Baustelle (nicht dieser Schritt): `Config.effective_endpoints` greift
  per Deferred-Import in den Katalog des gitlab_api-Guards — dieselbe Krankheit wie
  AppContext, umgekehrte Richtung. Kandidat: Aktivierungs-Parsing als Guard-Hook
  (`Guard.validate_config(cfg)`), Katalog-Zeug komplett raus aus `core/config.py`.
  Wird in Schritt F (Dataclass-Config) miterledigt.


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

### F. Config als transparente Dataclass + generischer TOML-Decoder

Ersetzt das ursprünglich geplante handgeschriebene `warden.toml`-Beispiel: wenn das
Schema eine verschachtelte Dataclass ist, *ist* das Schema die Doku — TOML-Tabellen
bilden 1:1 auf die Dataclass-Struktur ab.

- `core/toml_codec.py` (~60–80 Zeilen, keine neue Dependency — pydantic bleibt wegen
  Dependency-Disziplin an der Trust Boundary draußen): generisches
  `decode(cls, mapping) -> T`, rekursiv über `dataclasses.fields()` + Typ-Hints
  (Primitive, `tuple[...]`, `Optional`, verschachtelte Dataclasses). Fail-closed:
  unbekannter Key ⇒ `ConfigError`, fehlendes Pflichtfeld ⇒ `ConfigError`,
  Typ-Mismatch ⇒ `ConfigError`.
- Das Config-Schema wird verschachtelt und nach Besitz geschnitten: Core-Sektion
  (Modus, Ports, Pfade, Limits) in `core/config.py`; die gitlab-spezifische Sektion
  (Projekte, Branch-Namespace, `[api.endpoints]`-Aktivierung) als Dataclass im
  Guard-/Forge-Paket, vom Guard über einen Hook beigesteuert
  (`Guard.config_schema()` o.ä.). Damit fliegt der Deferred-Import-Trick
  (`Config.effective_endpoints` → Katalog) aus `core/config.py` raus.
- `catalog/config_parse.py` entfällt fast vollständig (mechanisches Mapping → Decoder);
  die *semantische* Validierung bleibt wo sie ist: Mode ⇒ Token-Pflicht in
  `config_load`, unbekannte Katalog-Ids/Capability-Verbote in `activation.py`.
- Env-/Secret-Handling (`*_FILE`-Indirektion, Env-über-File-Präzedenz) bleibt
  manuell in `config_load.py` — das ist kein TOML-Problem.
- Kein handgepflegtes Beispiel mehr; stattdessen ein Test, der ein aus den
  Dataclass-Defaults generiertes Minimal-TOML durch den Decoder round-trippt.
- Voraussetzung: H (der Override-Mechanismus mit freien Keys ist die eine Stelle,
  die sich nicht statisch typen lässt).

### H. Entfetten: Override-Mechanismus raus, Probes auslagern, errors entflechten

- **Override-Mechanismus löschen (YAGNI):** `OverridableParam`, `_apply_overrides`,
  `is_narrower`/`rebuild`, der `[api.endpoints.overrides]`-Parser und der eine
  Demo-Knopf `branch_prefix` auf `branch.create` („demonstrates the override
  mechanism end to end; no default entry needs it") — ~150 Zeilen Mechanik plus
  Tests für ein Feature ohne echten Nutzer. Die fail-closed-Validierung
  „unbekannte Id in `enable`" und das Capability-Verbot bleiben.
- **Deny-Probes aus `entries.py` auslagern** (eigenes `probes.py`, per Entry-Id
  zugeordnet): `entries.py` wird wieder eine lesbare Tabelle — eine Zeile pro
  Endpoint mit Methode/Template/Checks/Kind. Startgate-Mechanik unverändert
  (sie ist tragend: Boot-Abbruch, wenn eine Probe durchginge).
- **`git_reject_response` aus dem Root-`errors.py`** in den git-Guard verschieben;
  Root behält nur das Guard-agnostische (`deny_json`).

### G. Docstring-Pass: konzise und selbstgenügend

- Alle §/F/W/Schritt-/„Röst-Runde"-Referenzen und Migrations-Erzählung
  („pre-Schritt-5", „replaces the manual guard…") raus; was eine echte Invariante
  beschreibt (z. B. „record vor forward, damit ein Crash nie einen Write verliert"),
  bleibt — in einem Satz, aus sich heraus verständlich.
- Faustregel: Docstring erklärt *was gilt und warum*, nie *was vorher war* oder
  *wo es dokumentiert ist*. Zielgröße: Modul-Docstrings ≤ 5 Zeilen, Methoden ≤ 3.
- Zuletzt ausführen (nach allen anderen Schritten), damit der Pass nicht Text
  poliert, der ohnehin verschoben oder gelöscht wird.

### H2. Startgate abbauen (Vereinfachung, entschieden 2026-07-03)

Das Startgate hat zwei Rollen: (a) ein Golden-Test, der beim Boot statt in CI
läuft — das meiste (`state_event=merge` denied, Tag-Push denied, Branch außer
Namespace denied) steht schon in `test_policy`/`test_capabilities`/
`redteam/test_bypass`, also **redundant**; (b) Validierung der Deployment-
*Config* gegen die effektive Tabelle — der einzige nicht-redundante Wert. Rolle
(b) lebte fast ausschließlich vom Override-Mechanismus. **H hat die Overrides
gelöscht** ⇒ Config kann Einträge nur noch an-/abschalten, und `activation.py`
validiert das bereits fail-closed direkt (unbekannte Id, FORBIDDEN-Capability).
Damit schmilzt (b) weg und das Startgate dupliziert nur noch CI-Golden-Tests.

Vorgehen:

- Entry-spezifische Deny-Probes (`catalog/probes.py`) in normale pytest-Fälle
  überführen (Testwert bleibt).
- Die zwei globalen Invarianten (Merge-nie, Projekt-Grenze-immer) als 2–3
  Unit-Tests behalten; die Config-Validierung bleibt in `activation.py`.
- Löschen: `startgate.py`, `DenyProbe`, `catalog/probes.py`, `builtin.py`s
  `BUILTIN_DENY_PROBES`, `PROBE_PROJECT`/`OTHER_PROJECT`, die duplizierte
  Projekt-Regex in `startgate.py`, `StartgateFailure` und dessen Handling in
  `__main__.py`.
- `is_builtin_merge_endpoint` bleibt (das ist Laufzeit-Policy, kein Probe-Ding).

### I. Katalog auf `Recognizer → ⟨Capability, Scope⟩` vereinheitlichen (entschieden 2026-07-03)

Die halbe Idee existiert schon: `core/capabilities.py` **ist** die globale,
geschlossene Capability-Registry am Wurzel — jeder Guard mappt seinen Intent
dorthin (`git_ref_capabilities`, `api_capabilities`), der Kernel prüft gegen
`FORBIDDEN`. Der Umbau ersetzt die Trias `template` + `decision_fields` +
`checks`-Tupel pro Katalog-Eintrag durch *einen* Recognizer, der (a) match/kein
Match sagt und (b) bei Match die immer gebrauchten Zusatzinfos (Scope:
Branchname, iid, „braucht Owner-Lookup") normalisiert zurückgibt.

Beobachtung aus dem Durchmappen: **jeder** heutige Write-Check reduziert sich auf
⟨Capability-Set + Branch-Scope-Feld + Ownership-Scope⟩. `mr.create` →
`branch=source_branch`; `mr.note/discussion/reply` → `owner(iid)`; `mr.update` →
`owner(iid)` + Merge-Alias (schon Capability `MERGES`, der `field_not_equals`
ist redundant); `pipeline.trigger` → `branch=ref`; `branch.create` →
`branch=branch` + `CREATES_REF`; `issue.create` → nur Projekt-Grenze+Quota. Der
Scope-Raum ist winzig und geschlossen: `branch-namespace`, `mr-ownership`,
`quota-by-kind`, plus `content-exposure` auf der Read-Seite. Damit **vereinen
sich die zwei parallelen Policy-Mechanismen** (Read-Tabelle liefert terminale
Decision, Write-Katalog liefert `Optional[Decision]`) zu einem: Recognizer →
⟨cap, scope⟩ → *eine* generische `decide`.

Drei Dinge dürfen dabei NICHT verloren gehen (sonst hat man die Checks nur in
Matcher umbenannt): (1) Capabilities bleiben geschlossenes Core-Vokabular; (2)
Scope bleibt ein kleiner, geschlossener Satz normalisierter Felder, den *eine*
`decide` konsumiert — keine Ad-hoc-Logik pro Eintrag; (3) Feld-Extraktion bleibt
geteilt (F12: Body/Query nie blind mergen). Der Recognizer ist eine **Dataclass
mit Metadaten** (id/method/template) + schmaler match/extract-Funktion, keine
beliebige Funktion — sonst stirbt die `/policy`-Introspektion und die generische
fail-closed-Validierung. Der Umbau fasst §04 komplett an (dieselben Dateien wie
F); erst nach G angehen, wenn H/F den Katalog schon geschlankt haben.

## Beobachtungen ohne eigenen Schritt (bei Gelegenheit)

- die Ownership-regel (keine MRs ändern die dem agent nicht gehören) ist vermutlich übertrieben vorsichtig.
  Es würde vermutlich reichen zu fordern keine MRs anzufassen, die nicht von einem erlaubten branch starten. 
  Dadurch fallen auch wieder viele sonderfälle und sonderbehandlungen weg und für den endnutzer ist die Funktionsweise viel klarer.
- die State-Migrations sind unnötig, weil das Projekt noch komplett neu ist - weg damit
- **Zwei parallele Policy-Mechanismen:** die Read-Tabelle (`read_endpoints.py`,
  `ReadCheck` liefert immer eine terminale Decision) und der Write-Catalog
  (Checks liefern `Optional[Decision]`) haben unterschiedliche Formen — verteidigbar
  (Reads haben keinen „denied unless proven"-Default pro Zeile), aber man muss zwei
  Systeme lernen. Kandidat für Vereinheitlichung.
  
  Es wäre schön, wenn man die ReadEndpoints vereinfachen könnte.
  Das Problem ist aktuell, dass sich die API ja ändern kann und man dann sie immer spiegeln muss für die sachen, die man erlauben will.
  Wenn man immer wüsste, dass es nur ein READ ist eine Anfrage, könntem man sich darauf verlassen, dass das read-only token gefährliche anfragen blockt.
  Natürlich bräuchte man dann einen zuverlässigen check, ob das token wirklich readonly ist.
  Dann könnte man folgendes machen: statt zuerst zu schauen ob etwas read ist, match man gleich die write endpoints. Wenn es keiner davon ist, geht man davon aus, dass es entweder 
  ein unzulässiger write ist oder ein readonly. Man könnte den unzulässigen write dann einfach mit dem readonlytoken durchlassen; er würde ja scheitern.
  Um es noch etwas sicherer zu machen, kann man zusätzlich alle POST/PUT header filtern, die nicht den WriteEndpoint-Check passieren.
  Was übrig bleibt wird einfach durchgelassen. Das würde auch zukünftige Erweiterungen sehr vereinfachen, denn dann gäbe es gar keine explizitne Readendpoints mehr. Damit 
  wäre die oben genannten "Vereinheitlichung zwischen read/write endpoints" automatisch erfüllt.
  Das ganze steht und fällt mit dem check ob es wirklich nur ein READONLY-token ist. Man könnte natürlich darauf vertrauen, dass nur GET Anfragen schon nichts ändern, aber das ist nicht sicher.
  Eventuell nur eine Liste mit gefährlichen GETs führen, gegen die dann gecheckt wird wenn man im Read-only branch ist. Mir ist klar,dass das die sicherheit auch nicht garantiert; wenn die api sich erweitert ist man aufgeschmissen. Alles natürlich unter der voraussetzung das eigentliche read-only token ist keins. Auf projektebene kann ja immer noch gefiltert werden entsprechend
  der allowed projects wie bisher vor allen weiteren decisions; das schränkt zumindest den potenziellen zerstörungskreis ein. 

- **Logging per `print(..., file=sys.stderr)`** in `context.py`/`__main__.py` statt
  eines Loggers — für eine Trust-Boundary-Komponente dünn; ein `logging`-Logger mit
  festem Format wäre angemessen (kein neues Dependency nötig). Das könnte dann auch in eine .catraz/log/warden.log datei geschrieben werden.
- **`startgate.py` dupliziert die Projekt-Regex** aus `parsing.py`, um einen
  Import-Zyklus zu vermeiden — Symptom dafür, dass `parsing.py` zu viel vom Catalog
  weiß (importiert ihn für `CatalogEntry`/`Location`). Entzerrt sich womöglich mit
  F/H; danach prüfen, ob die Duplikation fallen kann.
- ein globales formatierungstool sollte alle dateien in der CI formatieren/prüfen. ruff format sollte das machen.
- Wichtige neue wünschenswerte Regel/Eigenschaft der Architektur: jeder Guard soll für sich stehen und keine Abhängigkeiten zu anderen Guards haben, wie das aktuell
  zwischen git und gitlab ist. Dies bedeutet, dass man für git ohne gitlab/github eigene sicherheitsregeln aufstellen muss, die dann unabhängig von der existenz eines gitlabs befolgt werden müssen. Es könnte ja auch sein, dass man einen privaten git-server betreibt, der kein Forge ist, dann muss der Guard auch funktionieren. Für die Regeln reicht es vermutlich zu fordern:
  1. Nicht mehr als X branches gleichzeitig auf dem remote die ein gewissen anzugebendes präfix haben
  2. auf andere branches als solche die das präfix haben dürfen erst gar nicht gepusht werden
  3. mehr als X commits pro Stunde sind verboten
  4. commits die größer sind als X sind verboten (in Kilobyte)
  5. Löschen von branches, tags etc. ist grundsätzlich verboten
  6. wenn gewünscht, kann der warden beliebigen text an den commit anhängen/voranstellen um kenntlich zu machen, dass es durch den warden ging

  Intern könnte das so gelöst werden, dass man nach einem fetch ganz simpel per git commands die branches zählt die relevant sind (präfixbranches), sich die anzahl commits anschaut anhand ihres Zeitstempels um zu entscheiden ob wieder committet werden darf (vergleich mit aktueller zeit) und weiters bei jedem push die größe des http-bodys ganz simpel bestimmen und wenn sie über
  der konfigurierten größe ist ablehnen. Damit spart man sich einen extra state. Wenn sich das als zu langsam/unpraktisch herausstellt, kann man immer noch eine weitere datenbanktabelle einführen in der selben sqlite-tabelle.

  Der Gitlab-Guard operiert wie gewohnt, ohne Fähigkeiten von git zu benötigen. So kann man sich den GitForge sparen und dessen funktionalität sauber in den GitlabGuard integrieren (bzw. hilfsfunktionen können gern als helper in anderen files sein, damit die eine datei nicht zu groß wird).

  Das Grundziel dieser Trennung ist Klarheit und Einfachheit, was wieder Sicherheit erhöht.

- durch die Trennung der Guards ist es prinzipiell möglich mehrere Warden-container, je einen pro guard, zu haben. Weiterhin sollte es möglich sein, dass git/gitlab mehrere Targets haben kann. Wenn ich zwei verschiedene git-server in einem catraz-ordner habe, soll es doch gehen beide zu bedienen. Das fehlt noch grundsätzlich im aktuellen design. Eine mögliche Lösung scheint nichttrivial, da brauchen wir noch ideen. Eventuell kann man den insteadof-trick weiterverwenden für git um je nach git-instanz zwar auf den warden umzuleiten, allerdings die domain wieder zu integrieren als ersten pfadparameter und das dem warden so klar machen. Dazu müssen sie zusammenarbeiten, was bisher auch der fall ist, aber nicht optimal. Grundsätzlich könntem man ja im warden.toml eine sektion haben wie
    [git.urls]
    gitlab.com
    my-gitlab.de
    personal-gitserver.it
    github.com
  die dann per insteadof im entrypoint abgearbeitet wird so dass z.b. personal-gitserver.it/myrepo.git -> warden:8080/personal-gitserver.it/myrepo.git umgeleitet wird, und der warden macht 
  es dann wieder zu der ursprünglichen adresse im upstream. Muss man durchdenken.
  Die Frage ist dann noch, wie man es z.b. bei mehreren gitlab-endpoints für die API macht. Vielleicht geht es auch so, dass man nicht in der warden.toml diese einstellungen vornimmt, sondern im .env die liste der erlaubten urls für den warden angibt, dann kann der nämlich alles auf sich umleiten per dns-einstellung im docker-compose und aus dem http-header wenn er version 1.1 ist 
  lesen was das eigentliche ziel war. Mir würde es im warden.toml aber besser gefallen, da dann alles beisammen ist. Das geht vermutlich sogar, wenn der entrypoint dns-einstellungen manipuliert.
  Ziel sollte es grundsätzlich sein bei der sache, dass man im container die interfaces gti/gitlab-api etc so nutzen kann wie man das außerhalb tun würde


