# 07 — Offene Verbesserungen (Backlog nach dem Migrationsplan)

Dieses Dokument sammelt die Verbesserungen, die **nicht** Teil der Schritte A–H2
des Migrationsplans (`TODO.md`, `## Plan: Architektur-Ausbesserung`) sind, aber
entschieden umgesetzt werden sollen. Jeder Abschnitt ist eigenständig shipbar und
so geschrieben, dass ein Sonnet-Subagent ihn ohne Rückfragen umsetzen kann:
**Ziel → Umsetzung → Nicht tun → Tests → Fertig-Kriterium.**

Reihenfolge = sinnvollste Umsetzungsreihenfolge (Abhängigkeit + Risiko + Größe).
Schritt 7 (Katalog-Vereinheitlichung) ist der frühere Plan-Schritt **I**, hierher
verschoben und angepasst: die Ownership-Lockerung (Schritt 4) ist bereits
eingearbeitet, es gibt kein autorbasiertes `mr-ownership`-Konzept mehr.

**Arbeitsmodus (für jeden Schritt gleich):** betroffene Dateien lesen, Änderung
machen, Verifikation laufen lassen, Diff-Review, ein einzelner Commit. Verifikation
für Warden-Änderungen immer:

```
cd warden
uv run pytest -q        # aktuell: 348 passed — darf nur durch bewusst geänderte/neue Tests steigen/fallen
uv run ruff check .
uv run mypy
```

Verifikation für CLI-Änderungen:

```
uv run --with pytest python -m pytest tests/cli/ tests/container/ -q
uv run mypy
```

Alle Pfadangaben sind relativ zum Repo-Root. Der Warden-Code liegt unter
`warden/warden/`, seine Tests unter `warden/tests/`.

---

## 1. `ruff format` in der CI erzwingen (beide Packages)

**Ziel.** Die CI prüft heute nur `ruff check` (Lint), nicht die Formatierung, und
die CLI-CI kennt ruff gar nicht. Formatierung soll deterministisch und
maschinell erzwungen sein — kein manueller Stilstreit im Review. **Zuerst
umsetzen**, weil der einmalige `ruff format`-Lauf viele Dateien anfasst; danach
bleiben alle Folge-Diffs sauber.

**Umsetzung.**

1. Ruff-Formatter-Config festnageln, damit „format" reproduzierbar ist. In beide
   pyproject-Dateien (`pyproject.toml` im Root für die CLI, `warden/pyproject.toml`
   für den Warden) einen Ruff-Abschnitt mit fixierter Zeilenlänge ergänzen — der
   Code liegt heute bei ~100 Zeichen, also:

   ```toml
   [tool.ruff]
   line-length = 100
   ```

   Falls schon ein `[tool.ruff]`-Abschnitt existiert, nur `line-length` ergänzen,
   nichts anderes anfassen.
2. Einmalig normalisieren und als **separaten Prep-Commit** committen (nicht mit
   dem CI-Change vermischen, sonst ist der CI-Diff unlesbar):
   - Root: `uv run ruff format .`
   - Warden: `cd warden && uv run ruff format .`
3. CI-Steps ergänzen:
   - `.github/workflows/warden-ci.yml`: **nach** dem Step „Lint (ruff)"
     (`uv run ruff check .`) einen Step „Format-Check (ruff)" mit
     `uv run ruff format --check .` einfügen.
   - `.github/workflows/cli-ci.yml`: ruff fehlt komplett. Nach „Install uv" /
     „Sync"-Logik einen Step ergänzen, der `uv run ruff check .` **und**
     `uv run ruff format --check .` ausführt. (Die CLI-CI nutzt heute
     `uv run --with …` für pytest; für ruff analog `uv run --with ruff ruff check .`
     bzw. `--with ruff ruff format --check .`, falls ruff nicht in den Dev-Deps des
     Root-`pyproject.toml` steht — dann ruff dort besser in `[dependency-groups] dev`
     aufnehmen und ohne `--with` aufrufen, konsistent zum Warden.)

**Nicht tun.** Keine anderen Ruff-Regeln (Lint-Regelsätze, `select`/`ignore`)
in diesem Schritt ändern — nur Formatierung. Keine Reformatierung mit dem
CI-Change im selben Commit.

**Tests.** Kein Unit-Test. Verifikation:
`uv run ruff format --check .` (Root **und** Warden) muss grün sein, ebenso
`ruff check` und `mypy`. Der Prep-Commit darf ausschließlich Whitespace/Umbruch
ändern — im Diff dürfen keine Logikänderungen auftauchen.

**Fertig-Kriterium.** Beide CI-Workflows brechen ab, wenn eine Datei nicht
`ruff format`-konform ist; der Baum ist einmal durchformatiert.

---

## 2. State-Migrationen entfernen (Version-Stamp + Fail-closed behalten)

**Ziel.** Das Projekt ist Pre-1.0; Container/State sind faktisch neu erzeugbar.
Die versionierte Migrations-Maschinerie (`core/state_migrations.py`) enthält u.a.
eine **No-op-Migration** (v1→v2), deren Docstring selbst sagt, sie existiere nur,
„to prove the runner can carry a step". Das ist Infrastruktur, die ihre eigene
Existenz rechtfertigt — weg damit. Was **bleibt**, ist die billige, echte
Absicherung: ein Schema-Versions-Stamp plus Fail-closed, wenn die DB neuer ist,
als dieser Build versteht.

**Umsetzung.**

1. `core/state.py`: In `State.__init__` steht heute `run_migrations(self._db)`
   (vor den `CREATE TABLE IF NOT EXISTS`). Diesen Aufruf ersetzen durch eine
   direkte, einfache Versionslogik:
   - `PRAGMA user_version` lesen.
   - Ist sie `> CURRENT_SCHEMA_VERSION` → `SchemaError` werfen (Fail-closed,
     A9 — ein Downgrade darf nie gegen eine unbekannte Form laufen).
   - Ist sie `0` oder `== CURRENT_SCHEMA_VERSION` → nichts tun; die
     `CREATE TABLE IF NOT EXISTS` erzeugen die aktuelle Form.
   - Danach `PRAGMA user_version = CURRENT_SCHEMA_VERSION` setzen.
   - `CURRENT_SCHEMA_VERSION` als `Final[int]` (Wert `1`) in `state.py`
     definieren. Die Tabellen in `state.py` müssen bereits die **Zielnamen**
     tragen (`agent_branches`, `agent_mrs`, `writes.guard`) — das ist nach
     Migrationsschritt 6/F11 ohnehin der Fall.
2. `core/state_migrations.py` **löschen** (`Migration`, `MIGRATIONS`,
   `run_migrations`, `BASE_SCHEMA_VERSION`, die `_stamp_*`/`_rename_*`-Funktionen).
   `SchemaError` nach `core/state.py` verschieben (es bleibt gebraucht) und die
   Re-Exports in `state.py` (`__all__`, der `from .state_migrations import …`)
   entsprechend anpassen: nur noch `SchemaError` und `CURRENT_SCHEMA_VERSION`
   exportieren.
3. Importer von `state_migrations` suchen (`grep -rn state_migrations warden/`) und
   auf `state` umbiegen. `__main__.py` fängt `SchemaError` beim Startup ab — Import
   dort auf `core.state` umstellen.

**Nicht tun.** Den Version-Stamp **nicht** ersatzlos streichen — die
Fail-closed-Prüfung „DB neuer als Build" ist der eine echte Wert und kostet fast
nichts. Keine „leere" Migrations-Abstraktion als Platzhalter zurücklassen.

**Tests.** `warden/tests/test_state.py` prüft heute Migrationsverhalten. Anpassen:
- Migrations-spezifische Fälle (v1→v2→v3-Lift, `run_migrations`) entfernen.
- Neu/behalten: (a) frische DB bekommt `user_version == CURRENT_SCHEMA_VERSION`;
  (b) eine DB mit `user_version` **größer** als `CURRENT_SCHEMA_VERSION` lässt
  `State(...)` mit `SchemaError` fehlschlagen; (c) eine frische DB hat die
  Zieltabellen (`agent_branches`, `agent_mrs`) und `writes` mit Spalte `guard`.
Verifikation: `cd warden && uv run pytest -q` grün, `ruff check`, `mypy` grün.

**Fertig-Kriterium.** `core/state_migrations.py` ist weg, `State` stampt und prüft
die Version selbst, kein Migrationslauf mehr, Tests grün.

> **✅ ERLEDIGT** (Commit `refactor(state): Punkt 2`). `state_migrations.py`
> gelöscht; `StateStore._check_and_stamp_schema_version()` liest/stampt
> `PRAGMA user_version` gegen `CURRENT_SCHEMA_VERSION=1`, fail-closed via
> `SchemaError` (jetzt in `state.py`). Migrationstests entfernt, Version-Stamp-/
> Fail-closed-/Zieltabellen-Tests behalten. pytest/ruff/format/mypy grün.

---

## 3. Betriebs-Logging: `print(…, file=sys.stderr)` → `logging.Logger` in Datei

**Ziel.** Der Warden ist eine Trust-Boundary-Komponente, loggt aber betrieblich
per nacktem `print(stderr)` (in `__main__.py`, `guards/gitlab/forge.py`). Kein
Level, kein Format, keine Datei. Ersetzen durch einen stdlib-`logging`-Logger mit
festem Format, der zusätzlich in eine Datei unter dem bind-gemounteten Log-Ordner
schreibt (`/var/log/warden/warden.log` → auf dem Host `.catraz/logs/warden/`).

**Umsetzung.**

1. `core/config.py`: neben `audit_log_path` ein Feld
   `log_path: str = "/var/log/warden/warden.log"` ergänzen (gleiches Verzeichnis
   wie das Audit-Log, das bereits bind-gemountet ist).
2. Logging einmal zentral konfigurieren. In `__main__._serve` **ganz am Anfang**
   (vor `from_env()`-Folgelogik, jedenfalls vor dem ersten Log-Aufruf) einen
   Helfer aufrufen, z.B. `configure_logging(cfg.log_path)`, der:
   - einen `logging.Formatter` mit festem Format setzt
     (`"%(asctime)s %(levelname)s %(name)s: %(message)s"`),
   - einen `StreamHandler` (stderr) **und** einen `FileHandler` auf `cfg.log_path`
     einhängt (Verzeichnis vorher `mkdir(parents=True, exist_ok=True)`),
   - das Root-Level auf `INFO` setzt.
   Den Helfer in ein kleines Modul `warden/warden/core/logging_setup.py` legen.
3. Jede Betriebs-`print(…, file=sys.stderr)` durch einen Modul-Logger ersetzen:
   `log = logging.getLogger("warden")` (oder `__name__`) oben im Modul, dann
   `log.warning(...)` / `log.error(...)` statt `print`. Betroffen: `__main__.py`
   (periodischer Reconcile-Fehler, leere-allowlist-Warnung, „initial reconcile
   incomplete") und `forge.py` (Reconcile-Fehler; die Service-Account-Warnung
   verschwindet ohnehin mit Schritt 4).

**Nicht tun.**
- **Nicht** das Audit-Log (`core/audit.py`, `audit.jsonl`) mit dem Betriebs-Log
  vermischen. Das Audit-Log ist ein separates, strukturiertes, versioniertes
  Sicherheitsprotokoll und bleibt unangetastet — auch sein eigenes `print` bleibt,
  falls es eines gibt, es sei denn es ist rein betrieblich.
- Kein JSON-Logging-Framework, kein neues Dependency. Nur stdlib `logging`.
- Kein Log-Rotation-Gold-Plating in diesem Schritt (die Datei wächst; Rotation ist
  ein separates, späteres Thema, analog zur Agent-Log-Persistenz im CLI-TODO).

**Tests.** `warden/tests/test_main.py` ist der natürliche Ort. Minimaltest:
`configure_logging(tmp_path/"warden.log")` aufrufen, eine Warnung loggen, prüfen
dass die Datei existiert und die Zeile enthält. Kein Test soll auf konkrete
stderr-Strings von vorher prüfen — solche Assertions ggf. auf den Logger
(`caplog`) umstellen. Verifikation: pytest/ruff/mypy grün.

**Fertig-Kriterium.** Keine betrieblichen `print(stderr)` mehr im Warden;
`.catraz/logs/warden/warden.log` füllt sich beim Lauf; Audit-Log unverändert.

> **✅ ERLEDIGT** (Commit `feat(logging): Punkt 3`). Neues
> `core/logging_setup.py::configure_logging()` (stderr + FileHandler auf
> `cfg.log_path`, festes Format, Root INFO, idempotent). `cfg.log_path`-Feld
> ergänzt; betriebliche prints in `__main__.py` + `guards/gitlab/forge.py` auf
> Modul-Logger umgestellt. Bewusst *nicht* angefasst: `core/audit.py`-prints
> (Audit-Subsystem) und der Startup-Abort-`print` (läuft vor `configure_logging`).
> pytest/ruff/format/mypy grün.

---

## 4. Ownership-Lockerung: MR-Zugriff an Branch-Namespace statt an Autor binden

**Ziel.** Heute darf der Agent einen MR nur anfassen (Notes, Discussions,
Update), wenn er **prefixed UND vom Service-Account authored** ist
(`forge.mr_owned_by_agent`). Das blockiert einen realen, erwünschten Workflow:
ein Kollege öffnet einen MR von einem erlaubten `claude/…`-Branch und delegiert
die Iteration an den Agenten — heute verweigert der Warden das, weil der Autor
der Kollege ist. Neue Regel: **Der Agent darf einen MR anfassen, wenn dessen
`source_branch` im erlaubten Branch-Namespace liegt** — unabhängig davon, wer
den MR erstellt hat. Dieselbe Achse (`in_branch_namespace`) wie bei `mr.create`.
Merge bleibt unabhängig verboten (Capability `MERGES` ∈ `FORBIDDEN`).

Sicherheits-Begründung (für den Umsetzer zum Verständnis, nicht zum Diskutieren):
Das Bedrohungsmodell ist „Blast-Radius **des Agenten** begrenzen", nicht „gegen
vertrauenswürdige Repo-Mitglieder verteidigen". Fremde MRs aus fremden Branches
bleiben durch die Namespace-Regel blockiert; ein Namespace-Branch ist ohnehin der
exklusive Push-Bereich des Agenten. Der Autor-Check hatte damit nur marginalen
Grenznutzen und kostete ein ganzes Konzept (Service-Account-Identität).

**Umsetzung.**

1. `guards/gitlab/forge.py`:
   - `mr_owned_by_agent(project, iid)` umbauen zu „ist `source_branch` des MR im
     Namespace?" — die Zeile
     `ok = self.cfg.in_branch_namespace(source) and sa is not None and author_id == sa`
     wird zu `ok = self.cfg.in_branch_namespace(source)`. Der Rückgabe-Kontrakt
     bleibt `Optional[bool]` (None bei Lookup-Fehler → Deny bleibt). Sinnvoll die
     Methode umbenennen (z.B. `mr_source_in_namespace`) und alle Aufrufer
     nachziehen — der Name soll nicht mehr „owned" behaupten.
   - Service-Account-Maschinerie entfernen, die dadurch **tot** wird:
     `resolve_service_account`, das Feld `service_account_id`, der `GET /user`-Call.
     Vorher verifizieren, dass es keine weiteren Nutzer gibt (`grep -n
     "service_account\|resolve_service_account\|author_id" warden/warden`).
   - `_list_agent_mrs(pid, sa)`: den `&author_id={sa}`-Filter entfernen; die Liste
     zählt jetzt **alle** offenen MRs, deren `source_branch` im Namespace liegt
     (konsistent mit der neuen Regel). Signatur auf `_list_agent_mrs(pid)`
     verschlanken; in `reconcile` den `sa`-Parameter und den
     `resolve_service_account()`-Aufruf entfernen.
2. `guards/gitlab_api/catalog/checks.py`: `_owned_by_agent` / `OWNED_BY_AGENT`
   liest weiterhin nur `intent.mr_owner_ok` (den Bool) — der Check-Body bleibt
   **unverändert**, nur die Bedeutung des Bools ändert sich. Umbenennen zu etwas
   Ehrlichem (`MR_SOURCE_IN_NAMESPACE` / `mr_source_ok`) und die Verweise in
   `entries.py` (`mr.note`, `mr.discussion`, `mr.discussion_reply`, `mr.update`)
   und `catalog/__init__.py` nachziehen. Der `needs={"mr_owner"}`-Marker bleibt
   (der iid-Lookup passiert weiterhin — der `source_branch` steht nicht im
   Request, nur die iid).

**Nicht tun.**
- Den Upstream-Lookup (iid → MR) **nicht** einsparen wollen — `mr.note`/`mr.update`
  tragen den `source_branch` nicht im Request, der Fetch bleibt nötig. Die Tristate
  (`True`/`False`/`None`-fail-closed) bleibt deshalb erhalten. Diese Änderung
  vereinfacht das **Policy-Konzept** (Autor/Service-Account weg), nicht die Mechanik.
- `state_event=merge` **nicht** freigeben — Merge bleibt via Capability `MERGES`
  verboten. (Der separate `field_not_equals("state_event","merge")`-Check darf
  bleiben, bis Schritt 7 ihn als redundant auflöst.)

**Tests.**
- `warden/tests/test_forge.py`: Ownership-Tests umschreiben — ein MR mit
  Namespace-`source_branch` aber **fremdem** Autor ist jetzt `True` (früher
  `False`); ein MR mit Nicht-Namespace-Branch bleibt `False`; Lookup-Fehler bleibt
  `None`. Service-Account-Auflösungstests entfernen.
- `warden/tests/test_forge_state.py`: Reconcile-Test anpassen — MR-Zählung nun
  autor-unabhängig, nur Namespace-gefiltert.
- `warden/tests/redteam/test_bypass.py`: sicherstellen, dass ein Nicht-Namespace-MR
  weiterhin abgelehnt wird (die Grenze, die zählt).
- Verifikation: pytest/ruff/mypy grün.

**Fertig-Kriterium.** Der Agent darf MRs auf erlaubten Branches unabhängig vom
Autor bearbeiten; Service-Account-Identität existiert nicht mehr als Konzept;
Merge bleibt blockiert; Tests grün.

> **✅ ERLEDIGT** (Commit `feat(forge): Punkt 4`). `mr_owned_by_agent` →
> `mr_source_in_namespace` (nur noch `in_branch_namespace(source)`, Tristate
> bleibt). Service-Account-Maschinerie komplett entfernt: `resolve_service_account`,
> `service_account_id`, `GET /user`, `author_id`-Filter in `_list_agent_mrs`,
> `ApiGuard.startup`. `intent.mr_owner_ok` → `mr_source_ok`; Check
> `OWNED_BY_AGENT` → `MR_SOURCE_IN_NAMESPACE`. `field_not_equals("merge")`
> bewusst belassen (Schritt 7). Redteam: Nicht-Namespace-MR bleibt R3-Deny,
> Merge bleibt R4-Deny. 341 passed, ruff/format/mypy grün.

---

## 5. `__main__._serve` entflechten: generische Runtime vs. Composition Root

**Ziel.** `_serve()` in `warden/warden/__main__.py` vermischt zwei Rollen:
generischer Lifecycle (startup → reconcile-gate → serve → teardown) und
gitlab-spezifische Verdrahtung. Die vier Code-`TODO`s in dieser Datei zeigen alle
darauf. `context.py` **ist** bereits der Composition Root; nur das
gitlab-spezifische `Upstream` wird noch von außen (`__main__`) konstruiert und
durchgereicht. Ziel: `__main__` weiß nichts mehr über `Upstream`.

**Umsetzung.**

1. **`Upstream`-Konstruktion in den Composition Root ziehen** (löst das TODO
   „this leaks from gitlab_api"). Heute: `__main__._serve` baut `upstream =
   Upstream(cfg)` und ruft `build_context(cfg, upstream, state, audit)`.
   Umbauen: `build_context(cfg, state, audit)` konstruiert `Upstream(cfg)` selbst
   (in `context.py`, das `Upstream` ohnehin schon importiert) und übergibt es an
   `GitForge`. `__main__` importiert `Upstream` danach nicht mehr. Den Teardown
   (`await upstream.aclose()` im `finally`) über den Context zugänglich machen —
   `AppContext` hält bereits `forge`; entweder `ctx.forge.upstream` schließen oder
   eine `AppContext.aclose()`-Methode einführen, die `upstream.aclose()`,
   `audit.stop()`, `state.close()` bündelt. Letzteres bevorzugen: dann kennt
   `__main__` die einzelnen Ressourcen gar nicht.
2. **Server-Lifecycle extrahieren.** Den Uvicorn-Block (Agent-Server, Admin-UDS-vs-
   TCP-Verzweigung, `asyncio.gather`, `finally`-Teardown) in eine eigene
   Funktion `_run_servers(ctx)` ziehen. `_serve` wird dann linear lesbar:
   Config laden → (Startgate, siehe unten) → Context bauen → startup+reconcile-gate
   → `_run_servers(ctx)`.
3. **Startgate-Zeile:** In `_serve` steht heute `build_effective_table` +
   `run_startgate`. Das entfernt **Plan-Schritt H2** (Startgate-Abbau). Dieser
   Schritt hier soll H2 **nicht vorwegnehmen**: die Zeile so lassen, wie H2 sie
   hinterlässt. Falls H2 schon erledigt ist, ist hier nichts zu tun; falls nicht,
   diese Zeile in Ruhe lassen und nur Punkt 1+2 machen.

**Nicht tun.**
- Das vierte TODO („reconcile-before-open in den Gitlab-Guard verschieben")
  **nicht** umsetzen — **won't do**. Die Reihenfolge „reconcile, dann Port öffnen"
  ist eine globale Lifecycle-Garantie und gehört in die Runtime/den Composition
  Root, nicht in einen einzelnen Guard. Würde ein Guard den Port-Zeitpunkt
  steuern, kehrte sich die Kontrollrichtung um (schlechtere Kopplung). Die heutige
  polymorphe Schleife `for g in ctx.guards: await g.reconcile()` bleibt in
  `__main__`. Das TODO im Code beim Umbau entfernen und durch einen Einzeiler
  ersetzen, der festhält, dass das bewusst hier lebt.
- `_serve` **nicht** in viele Mini-Funktionen zerhacken „der Länge wegen". Der
  Composition-Root-Teil ist legitim linear. Nur `_run_servers` ist eine echte
  Naht.

**Tests.** `warden/tests/test_main.py`. Falls dort `build_context` mit vier
Argumenten aufgerufen wird, auf die neue Signatur (`cfg, state, audit`) anpassen.
Ein Test, der den Lifecycle bis „Port offen" simuliert, muss grün bleiben.
`serve_test.py` beachten. Verifikation: pytest/ruff/mypy grün.

**Fertig-Kriterium.** `__main__` importiert/konstruiert kein `Upstream` mehr;
`_run_servers` gekapselt; die vier Code-TODOs sind entweder umgesetzt (Upstream),
an H2 delegiert (Startgate) oder bewusst als won't-do vermerkt (reconcile-in-Guard).

> **✅ ERLEDIGT** (Commit `refactor(main): Punkt 5`). `build_context(cfg, state,
> audit)` konstruiert `Upstream` selbst; `AppContext` hält `upstream` + neue
> `aclose()` (bündelt upstream/audit/state-Teardown). `__main__` importiert
> `Upstream` nicht mehr; Uvicorn-Block in `_run_servers(ctx)` extrahiert, `_serve`
> linear. TODO „tidy up" + „leaks from gitlab_api" weg; reconcile-in-Guard-TODO
> durch Won't-do-Kommentar ersetzt. Startgate (5.3) war durch H2 bereits weg —
> No-op. 341 passed, ruff/format/mypy grün.

---

## 6. Guard-Unabhängigkeit: git-Guard eigenständig, Transport entkoppelt, `GitForge` auflösen

**Ziel.** Der git-Guard (`guards/git/guard.py`) importiert heute `GitForge` und
`stream_upstream` aus `guards/gitlab/` — die „one honest exception §03.3". Zwei
Ziele: (a) jeder Guard steht für sich, der git-Guard funktioniert auch **ohne**
GitLab (z.B. gegen einen privaten Non-Forge-git-Server); (b) **die Klasse
`GitForge` verschwindet ganz** — sie ist kein Guard, liegt aber im Guard-Bereich
(`guards/gitlab/`, weder Guard noch Transport), und nach Entzug des Transports
enthält sie nur noch GitLab-Spezifika, die ins API-Guard-Paket gehören.

**Wichtige Ausgangslage (Reihenfolge-Falle).** `GitForge` ist heute **geteilt**:
`context.py` injiziert es in `GitGuard` **und** `ApiGuard`. Der git-Guard nutzt
daran mehr als Transport:

- `reconcile()` baut in einem Rutsch **Branch**-Zähler (git) *und* MR-Zähler (API)
  aus GitLab neu auf und setzt `mark_reconciled()`/Unlock;
- `state_view()` kombiniert Core-Lock/Writes **+ Branch-Counts (git) + MR-Counts (API)**;
- `project_id_aliases` / `project_allowed_by_id` (numerische-ID-Auflösung für die
  R6-Projektgrenze), im Reconcile befüllt, von beiden Guards gelesen.

Deshalb darf man das Rest-`GitForge` **nicht** einfach in `ApiGuard` falten,
solange der git-Guard noch daran hängt — sonst tauscht man „git-Guard hängt an
`gitlab.forge`" gegen „git-Guard hängt an `ApiGuard`" (Guard→Guard), also die
**exakt verbotene** Kopplung. Die Fold kommt daher **zuletzt**, nach der
git-Verselbstständigung. Der Pfeil zeigt heute falsch: Transport ist
fundamentaler als der Forge.

Die git-nativen Sicherheitsregeln existieren bereits in `guards/git/policy.py`
(R2 Branch-Prefix, R4 kein Tag/Delete, R5 max offene Branches + Writes/Stunde) —
sie brauchen keinen Forge. **Ergänzt** wird nur eine Regel: Push-Größe begrenzen.

**Umsetzung (in dieser Reihenfolge).**

1. **Transport neutralisieren.** Die transport-artigen Teile von `GitForge`
   (`Upstream`-Nutzung, Credential-/Token-Injektion, `stream_upstream`) in einen
   forge-neutralen Baustein extrahieren — Vorschlag: `guards/git/transport.py`
   (oder `core/`, falls beide Guards ihn teilen). Der git-Guard hängt danach an
   diesem Transport, **nicht** an `guards.gitlab.forge`. Falls git-Guard und
   REST-Guard denselben Upstream-Pool teilen sollen (Connection-Pooling), injiziert
   der Composition Root **einen generischen** HTTP-Client — der git-Guard bekommt
   nie ein gitlab-benanntes Objekt.
2. **`git_reject_response`** aus dem Root-`errors.py` in den git-Guard verschieben
   (das ist ohnehin Plan-Schritt H; falls dort noch offen, hier miterledigen —
   Root behält nur guard-agnostisches `deny_json`).
3. **Push-Größenlimit (neue Regel).** In `guards/git/policy.py` bzw. im
   git-Guard-`enrich`/`decide`: die Größe des receive-pack-HTTP-Bodys gegen ein
   konfiguriertes Limit prüfen und bei Überschreitung mit R5 ablehnen. Umsetzung
   **billig und ohne Packfile-Parsing**: `Content-Length` bzw. die Länge des
   gelesenen Bodys heranziehen. Config-Feld `max_push_bytes` in `core/config.py`
   ergänzen (Default großzügig, z.B. einige MB).
4. **git-Guard bekommt eigenen State + `state_view`.** Damit der git-Guard nicht
   mehr am geteilten `GitForge`-Reconcile hängt: seinen Branch-/Rate-Zustand selbst
   führen (Core-`writes`/Lock bleibt Core; die Branch-Verfolgung wird git-eigen).
   `state_view()` aufsplitten — jeder Guard baut seinen eigenen Snapshot
   (Core-Lock + **eigene** Domänen-Counts), statt einen kombinierten aus dem Forge
   zu ziehen. Nach diesem Schritt liest der git-Guard **nichts** mehr aus
   `guards/gitlab/`.
5. **`GitForge` auflösen und in den API-Guard falten (`GitForge` als Klasse
   entfällt).** Erst wenn Schritt 4 steht, hat die Rest-Domänenlogik nur noch
   **einen** Konsumenten (den API-Guard):
   - `mr_source_in_namespace` (ehem. Ownership-Lookup, siehe Schritt 4 des
     Dokuments), MR-Reconcile (`_list_agent_mrs`, `_get_paginated`), die
     numerische-ID-Auflösung (`project_id_aliases`, `_resolve_project_id`,
     `project_allowed_by_id`) und der MR-Anteil von `state_view` wandern ins
     `guards/gitlab_api/`-Paket.
   - Kein Gott-Objekt: diese Helfer dürfen in Nachbardateien im `gitlab_api`-Paket
     liegen (`reconcile.py`, `ownership.py`, o.ä.), damit die Guard-Datei klein
     bleibt. Sie sind Implementierungsdetail **des** API-Guards, keine geteilte
     Klasse mehr.
   - `guards/gitlab/` löst sich auf: `forge.py` weg, `upstream.py` → in den
     neutralen Transport (Schritt 1), `state.py` (ForgeState/MRs) → ins
     `gitlab_api`-Paket. Das Paket, das „weder Guard noch Transport" war,
     existiert danach nicht mehr.

**Nicht tun.**
- **Rest-`GitForge` nicht vor Schritt 4 in `ApiGuard` falten.** Solange der
  git-Guard noch am geteilten Reconcile/`state_view`/Projekt-Alias hängt, erzeugt
  die Fold eine git→ApiGuard-Kopplung (Guard→Guard) — schlechter als heute. Erst
  Transport raus (1) und git-eigener State (4), **dann** falten (5).
- **Kein** Live-Zählen von Branches/Commits per `git fetch` im Entscheidungspfad.
  Das erforderte git-Binary + Netz-Roundtrip + Credentials nur für eine
  Entscheidung. Der State (Branch-/`writes_last_hour`-Zähler in SQLite) existiert
  genau, um das zu vermeiden — er bleibt die Quelle für Regel 1/3/5.
- **Kein** Umschreiben/Annotieren von Commits durch den Warden. Commit-Messages im
  Proxy zu verändern erzeugt neue SHAs, bricht Signaturen und lässt die gepushten
  Refs von dem abweichen, was der Agent lokal committet hat. Wenn „durch Warden
  gegangen" markiert werden soll: client-seitiger Trailer oder Audit-Record — nicht
  hier.

**Tests.**
- `warden/tests/test_git_proxy.py`, `test_git_e2e.py`, `test_forge.py`,
  `test_forge_state.py`, `tests/container/test_git_warden.py`: müssen das Verhalten
  über Transport-Extraktion, State-Split und Fold hinweg **grün** halten (gleiche
  Entscheidungen, gleiche Reconcile-Ergebnisse). Bei der Fold ziehen die
  `test_forge*`-Tests thematisch ins `gitlab_api`-Testpaket um.
- Neuer Test für das Push-Größenlimit: ein receive-pack-Request knapp über
  `max_push_bytes` wird mit R5 abgelehnt, knapp darunter durchgelassen (in
  `test_git_proxy.py` oder `test_policy.py`).
- **Kopplungs-Test (die Kernaussage dieses Schritts):** `guards/git/` importiert
  weder aus `guards/gitlab/` noch aus `guards/gitlab_api/`. Prüfen mit
  `grep -rn "from ..gitlab\|import gitlab" warden/warden/guards/git/` → keine
  Treffer. Und `guards/gitlab/` existiert nach Schritt 5 nicht mehr.
- Verifikation: pytest/ruff/mypy grün.

**Fertig-Kriterium.** `GitForge` als Klasse ist weg; `guards/gitlab/` existiert
nicht mehr; der git-Guard importiert keine Forge-/Guard-Fremdlogik und hat eigenen
State + Push-Größenlimit; die GitLab-Domänenlogik liegt im `gitlab_api`-Paket; die
git-nativen Regeln (R2/R4/R5 + Größe) stehen ohne Forge; Tests grün.

> **✅ ERLEDIGT** (Commit-Serie `§07 Punkt 6`). Strikt in der vorgegebenen
> Reihenfolge: (1) `guards/gitlab/upstream.py` → `core/transport.py`
> (`Upstream`, `stream_upstream`, `project_id`, neu `get_paginated`), von
> `context.py`, git- und `gitlab_api`-Guard importiert — git-Guard hängt seither
> nie mehr an `guards.gitlab`. (6.2 `git_reject_response` war schon in
> `guards/git/errors.py` — kein Rest-Import mehr, nichts zu tun.) (3)
> `max_push_bytes` (Default 50 MiB) in `Config`/`config_load.py`; `GitIntent.
> push_bytes` aus `Content-Length`; `policy.decide` lehnt Überschreitung vor dem
> Ref-Loop mit R5 ab, ohne Packfile-Parsing. (4) Branch-Tabelle/-Methoden →
> `guards/git/state.py::BranchState`; eigener Reconcile in
> `guards/git/reconcile.py::reconcile_branches` (nutzt nur `core.transport`);
> `GitGuard.state_view()`/`reconcile()` eigenständig, Core-Lock bleibt geteilt
> (wie im Dokument gefordert). (5) `GitForge` aufgelöst: MR-Ownership →
> `guards/gitlab_api/ownership.py::MrOwnership`, MR-Reconcile +
> Projekt-Id-Alias → `guards/gitlab_api/reconcile.py::reconcile_mrs`, MR-Tabelle
> → `guards/gitlab_api/state.py::MrState`; `ApiGuard` hält sie direkt (kein
> Gott-Objekt). `guards/gitlab/` komplett gelöscht (`forge.py`, `state.py`,
> `__init__.py`). `context.py`/`AppContext` kennen `GitForge`/`forge` nicht mehr,
> nur noch den geteilten `Upstream`. Nordstern-Grep leer, `ls guards/gitlab/`
> → nicht gefunden. Tests thematisch umgezogen (`test_git_state.py`,
> `test_git_reconcile.py`, `test_api_ownership.py`, `test_api_reconcile.py`,
> `test_api_state.py`); `test_forge.py` gelöscht. 347 passed (Warden) + 386
> passed (CLI/Container), ruff/format/mypy grün (Root + Warden).

---

## 7. Katalog auf `Recognizer → ⟨Capability, Scope⟩` vereinheitlichen (ehem. Schritt I, Ownership bereits ausgebaut)

> Dies war Plan-Schritt **I** und ist hierher verschoben, weil er kein reiner
> Migrationsschritt ist, sondern eine Konsolidierung. **Angepasst gegenüber dem
> Original:** die Ownership-Lockerung (Schritt 4) ist eingearbeitet — es gibt
> **kein autorbasiertes `mr-ownership`-Scope** mehr; MR-Zugriff fällt unter den
> `branch-namespace`-Scope (auf dem via iid aufgelösten `source_branch`).
>
> **Voraussetzung:** Migrationsplan-Schritte F, H, H2 und G sind abgeschlossen
> (der Katalog ist geschlankt, Overrides + Startgate sind weg, Docstrings sauber)
> **und** Schritt 4 dieses Dokuments ist umgesetzt.

**Ziel.** Heute gibt es zwei parallele Policy-Mechanismen: die Read-Tabelle
(`read_endpoints.py`, `ReadCheck` liefert immer eine **terminale** Decision) und
den Write-Katalog (`entries.py`, Checks liefern `Optional[Decision]`). Zwei Formen,
die man beide lernen muss. Beide werden zu **einem** Modell vereinigt: pro
Endpoint ein **Recognizer**, der (a) match/kein-Match sagt und (b) bei Match die
normalisierten Zusatzinfos (Scope) zurückgibt. Eine **einzige** generische
`decide` konsumiert `⟨Capabilities, Scope⟩`.

`core/capabilities.py` ist bereits die globale, geschlossene Capability-Registry;
jeder Guard mappt seinen Intent dorthin (`git_ref_capabilities`, `api_capabilities`),
der Kernel prüft gegen `FORBIDDEN`. Der Umbau ersetzt die Trias
`template` + `decision_fields` + `checks`-Tupel pro Katalog-Eintrag durch **einen**
Recognizer.

**Der geschlossene Scope-Raum (nach Ownership-Lockerung).** Jeder heutige
Write-Check reduziert sich auf ⟨Capability-Set + Scope⟩ mit genau diesen Scopes:

- `branch-namespace` — ein Branchname muss `in_branch_namespace` sein. Quelle je
  Endpoint:
  - `mr.create` → `source_branch` (Body-Feld)
  - `pipeline.trigger` → `ref` (Body-Feld)
  - `branch.create` → `branch` (Body-Feld), zusätzlich Capability `CREATES_REF`
  - `mr.note` / `mr.discussion` / `mr.discussion_reply` / `mr.update` →
    `source_branch`, **aufgelöst via iid-Lookup** (der Request trägt nur die iid).
    Das ist der frühere „owner(iid)"-Scope, jetzt ohne Autor-Check: reiner
    Namespace-Test auf dem aufgelösten `source_branch`.
- `quota-by-kind` — Projekt-Grenze + Quota (offene Branches/MRs, Writes/Stunde,
  Lock). Beispiel `issue.create`: nur Projekt-Grenze + Quota, kein Branch-Scope.
- `content-exposure` — die **Read-Seite** (`read_endpoints.py`): projektlose GETs,
  deren Terminal-Decision heute R1 (Metadaten erlaubt) oder R6 (Repo-Inhalt
  verweigert) ist. Bleibt als Scope erhalten — **default-deny bleibt**.

`mr.update` + `state_event=merge`: die Capability `MERGES` deckt das bereits ab
(∈ `FORBIDDEN`); der separate `field_not_equals("state_event","merge")`-Check ist
damit **redundant und entfällt** in diesem Schritt.

**Umsetzung.**

1. Recognizer als **Dataclass mit Metadaten** definieren (`id`, `method`,
   `template`) plus eine schmale `match`/`extract`-Funktion, die bei Treffer den
   Scope normalisiert zurückgibt (Branchname / iid / „braucht iid-Lookup" /
   Read-content-Klasse). **Keine beliebige Funktion** — sonst stirbt die
   `/policy`-Introspektion (Admin-App, `app.py:_policy`) und die generische
   fail-closed-Validierung.
2. Read- und Write-Einträge auf denselben Recognizer-Typ bringen. Die Read-Zeilen
   werden Recognizer mit Scope `content-exposure` und terminaler Klassifikation
   (R1/R6); die Write-Zeilen werden Recognizer mit `branch-namespace`/`quota-by-kind`
   + Capability-Set.
3. **Eine** generische `decide(intent, recognizer_match, state, cfg)` schreiben,
   die den Scope konsumiert — keine Ad-hoc-Logik pro Eintrag. Sie ruft für
   `branch-namespace` `in_branch_namespace` auf (bei iid-Scope zuvor den
   `source_branch` via `forge.mr_source_in_namespace`/iid-Lookup auflösen), für
   `content-exposure` die R1/R6-Klassifikation, für `quota-by-kind` die
   State-/Quota-Prüfung.
4. Feld-Extraktion bleibt **geteilt** (F12: Body/Query nie blind mergen) — die
   bestehenden `parsing.py`-Helfer weiterverwenden.
5. Betroffen ist §04 komplett: `entries.py`, `read_endpoints.py`, `checks.py`,
   `catalog/model.py`, `policy.py`, `guard.py`. `api_capabilities` bleibt (Mapping
   Intent→Capabilities), inkl. der field-abhängigen `MERGES`-Ergänzung.

**Drei Invarianten, die NICHT verloren gehen dürfen** (sonst hat man Checks nur in
Matcher umbenannt):

1. **Capabilities bleiben geschlossenes Core-Vokabular** (`core/capabilities.py`),
   Kernel prüft gegen `FORBIDDEN`.
2. **Scope bleibt ein kleiner, geschlossener Satz** normalisierter Felder, den
   *eine* `decide` konsumiert — keine Pro-Eintrag-Sonderlogik.
3. **Default-deny bleibt** auf der Read-Seite: alles, was kein Recognizer als
   erlaubte Metadaten (R1) erkennt, ist verweigert. (Ausdrücklich **nicht** die
   verworfene Idee „Reads durchlassen und dem Read-only-Token vertrauen" — das wäre
   ein Allowlist→Blocklist-Regress.)

**Nicht tun.**
- Kein autorbasiertes Ownership-Scope wieder einführen (Schritt 4 hat es entfernt).
- Recognizer **nicht** als freie Funktion — nur Dataclass + schmale
  match/extract-Funktion (Introspektion/Validierung hängen daran).
- Read-Tabelle **nicht** abschaffen/aufweichen — sie wird in `content-exposure`
  überführt, default-deny bleibt.

**Tests.**
- `warden/tests/test_policy.py`, `test_capabilities.py`, `test_api_proxy.py`,
  `tests/catalog/*`, `redteam/test_bypass.py`: müssen als Verhaltensnetz **grün
  bleiben** — der Umbau ist eine reine Umstrukturierung gleicher Entscheidungen.
- Neu absichern: (a) jede frühere Read-Terminal-Decision (R1 erlaubt, R6
  verweigert, `search?scope=blobs` verweigert) liefert nach dem Umbau dasselbe;
  (b) jeder Write-Endpoint entscheidet identisch; (c) unbekannter Pfad bleibt
  default-deny; (d) `state_event=merge` bleibt verboten (jetzt allein via
  Capability).
- Golden-/Report-Test der `/policy`-Introspektion (falls vorhanden,
  `endpoint_table_report`) an die neue Struktur anpassen, ohne Deckungslücke.
- Verifikation: pytest/ruff/mypy grün.

**Fertig-Kriterium.** Read- und Write-Pfad laufen über **ein** Recognizer→⟨cap,
scope⟩→`decide`-Modell; die drei Invarianten stehen; kein Autor-Ownership;
`field_not_equals("merge")` entfernt; identisches Entscheidungsverhalten; Tests grün.

---

## 8. Multi-Target: mehrere git-/Forge-Instanzen pro `.catraz` via Host-Routing

**Ziel.** Ein `.catraz`-Ordner soll mehr als eine Upstream-Instanz bedienen können
(z.B. `gitlab.com` **und** `my-gitlab.de` **und** ein privater git-Server). Heute
nimmt das Design genau einen Upstream an. Der saubere Mechanismus ist
**Host-basiertes Routing** — der Agent behält kanonische Remotes, der Warden
erkennt das echte Ziel am HTTP-`Host`-Header. Das erfüllt zugleich das Ziel „im
Container die Interfaces so nutzen wie außerhalb" und konvergiert mit **P5** im
CLI-`TODO` (transparente Warden-Interception, `insteadOf` ablösen).

> **Größter, sicherheitssensitivster Schritt. Zuletzt.** Voraussetzung:
> Schritt 6 (unabhängige, host-parametrische Guards). Er berührt die
> Trust-Boundary (welche Upstreams sind erreichbar), daher als **erster
> Teil-Deliverable ein kurzer Design-Spike** (eigene Datei
> `docs/design/architecture-generalization/08-multi-target.md`), der die zwei
> unten genannten offenen Detailfragen für die API-Seite entscheidet, **bevor**
> Code entsteht.

**Empfohlener Ansatz (entschieden, nicht mehr offen).**

1. **Konfiguration in `warden.toml`** (nicht `.env` — alles an einem Ort): eine
   Sektion, die die erlaubten Upstream-Hosts als **explizite, enumerierte
   Allowlist** listet (R5/§6.10 — offer, never auto-add), z.B.:

   ```toml
   [git.urls]
   hosts = ["gitlab.com", "my-gitlab.de", "personal-gitserver.it"]
   ```

   `core/config.py` liest das in ein `frozenset` erlaubter Hosts. Ein Request an
   einen nicht gelisteten Host wird abgelehnt (default-deny, konsistent mit R6).
2. **Routing per `Host`-Header.** Der Docker-DNS zeigt die gelisteten Hosts auf den
   Warden (Compose-`extra_hosts`/Netzwerk-Aliase; im Entrypoint gesetzt). Der
   Warden liest `request.headers["host"]`, prüft ihn gegen die Allowlist und wählt
   den passenden Upstream (Transport aus Schritt 6, pro Host parametrisiert:
   Basis-URL + Credentials). Damit behält der Agent kanonische Remotes
   (`git clone https://my-gitlab.de/x.git`) — kein `insteadOf`-Pfad-Trick, keine
   geleakte Warden-Adresse in den Remotes.
3. **Guards pro Host parametrisieren.** Der git-Guard ist nach Schritt 6 bereits
   transport-neutral; er bekommt den ziel-spezifischen Transport anhand des Hosts.
   Der GitLab-API-Guard analog, wo ein GitLab-Host adressiert ist.

**Zwei offene Detailfragen — im Spike (`08-…md`) zu entscheiden, dann umsetzen:**

- **API-Multi-Endpoint:** Wie werden mehrere GitLab-**API**-Instanzen adressiert
  (Host-Routing wie bei git, oder separate Guard-Instanzen)? Empfehlung:
  identisches Host-Routing; im Spike bestätigen.
- **Credentials pro Host:** Woher kommen Token je Host (getrennte Env-Variablen
  pro Host? Sektion in `warden.toml`)? Im Spike festlegen; Grundsatz: keine
  Geheimnisse in `warden.toml` — Token bleiben in der Umgebung, die Host-Liste in
  `warden.toml`.

**Nicht tun.**
- **Kein** `insteadOf`-Pfad-Präfix-Trick (`warden:8080/personal-gitserver.it/repo.git`):
  er macht die Remotes im Container un-kanonisch und widerspricht dem Kernziel.
  Host-Routing statt Pfad-Encoding.
- **Kein** separater Warden-Container pro Guard/Host „auf Vorrat" — ein Warden, der
  nach Host routet, ist einfacher und reicht. Container-Vervielfachung erst bei
  konkretem Isolationsbedarf.
- Die Host-Liste **nicht** implizit/automatisch füllen — explizite Allowlist.

**Tests.**
- Config-Test (`test_config.py`): `[git.urls].hosts` wird in die Host-Allowlist
  geparst; leere/fehlende Sektion → Verhalten wie heute (ein Default-Host oder
  leer→deny, im Spike festgelegt).
- Routing-Test: Request mit erlaubtem `Host` wählt den richtigen Upstream; Request
  mit nicht gelistetem `Host` wird abgelehnt (default-deny).
- Container-Test (`tests/container/`): zwei Hosts konfiguriert, beide erreichbar,
  ein dritter abgelehnt.
- Verifikation: warden pytest/ruff/mypy **und** CLI-Tests grün.

**Fertig-Kriterium.** Ein `.catraz` kann mehrere gelistete Upstream-Hosts bedienen;
der Agent nutzt kanonische Remotes; nicht gelistete Hosts werden verweigert; Spike
`08-multi-target.md` dokumentiert die zwei Detailentscheidungen.

---

## Verhältnis zum Migrationsplan (H2, I) und CLI-`TODO`

- **Ehem. Schritt I** ist Schritt 7 dieses Dokuments (angepasst: Ownership
  ausgebaut) und wurde aus `TODO.md` entfernt.
- **Schritt H2** (Startgate-Abbau) bleibt im Migrationsplan. Es erledigt nebenbei
  die früher notierte „duplizierte Projekt-Regex in `startgate.py`" (die Datei wird
  gelöscht) — daher taucht dieser Punkt hier **nicht** als eigener Schritt auf.
- **Verworfen** (nicht in diesem Backlog): „Read-Endpoints abschaffen und dem
  Read-only-Token vertrauen" — Allowlist→Blocklist-Regress; die legitime
  Vereinheitlichung liefert Schritt 7 unter Erhalt von default-deny.
- **CLI-seitige Punkte** (P1–P10 im `docs/design/TODO.md`) sind dort verortet;
  Schritt 8 hier konvergiert mit P5 (transparente Interception / `insteadOf`
  ablösen) und sollte mit ihm gemeinsam gedacht werden.
