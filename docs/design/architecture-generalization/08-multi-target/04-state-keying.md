# 04 — State-Keying `(host, project)` + per-Endpoint-Quote

**Leitet ab aus** [`../08-multi-target.md`](../08-multi-target.md) §5. Lies §5 zuerst.
**Hängt ab von** Schritt 01, 03.

## Ziel

Branch-/MR-Zustand nach `(host, project)` schlüsseln und die stateful Quotas
**per-Endpoint** zählen (Konsequenz der Regel-Kaskade, §3.3). `implicit_host` als
Schlüsselquelle entfällt; jeder Host stammt aus einem Endpoint.

## Umsetzung

Ist-Zustand: `guards/git/state.py::BranchState` und `guards/gitlab_api/state.py::MrState`
tragen bereits eine `host`-Spalte (Schema v2, `core/state.py::CURRENT_SCHEMA_VERSION`);
`reconcile_branches`/`reconcile_mrs` iterieren über `Config.effective_hosts`
(inkl. `implicit_host`); die Quote-Zähler (`open_branches`/`open_mrs`) zählen **global**
über alle Hosts.

1. **Reconcile pro Endpoint.** `reconcile_branches` (`guards/git/reconcile.py`) und
   `reconcile_mrs` (`guards/gitlab_api/reconcile.py`) über `Config.git_endpoints` laufen
   lassen (statt `effective_hosts`), je Endpoint gegen dessen Upstream (Schritt 03),
   `closed`-Endpoints überspringen. Schlüsselteil ist `endpoint.host`.
2. **Per-Endpoint-Zählung.** `BranchState.open_branches`/`MrState.open_mrs` (und der
   `writes_last_hour`-Zähler) auf **`host`-gefiltert** umstellen. Der Entscheidungspfad
   ruft sie mit `intent.host`. Die effektive Obergrenze kommt aus
   `Config.effective_rules(intent.host)` (Schritt 01) — nicht mehr aus einem globalen
   Feld.
3. **`implicit_host` endgültig raus.** Nach Schritt 03 hat `effective_hosts` keinen
   Single-Target-Zweig mehr; sicherstellen, dass kein Reconcile-/State-Pfad mehr auf
   `implicit_host` zugreift.
4. **Schema.** Die `host`-Spalte existiert bereits (v2). Falls Schritt 01–03 die
   Tabellenform sonst nicht ändern, ist **kein** weiterer Version-Bump nötig. Ändert sich
   eine Tabelle, `CURRENT_SCHEMA_VERSION` erhöhen (kein Migrationslauf — ältere DB wird
   via `_check_and_stamp_schema_version` fail-closed abgelehnt, State ist wegwerfbar).

## Nicht tun

- Kein Live-Zählen per `git fetch`/API im Entscheidungspfad — der SQLite-State bleibt die
  Quelle.
- Keinen un-überschreibbaren globalen Gesamtdeckel einführen (§7 im Hauptdokument): die
  Quote ist bewusst per-Endpoint.
- Keine Migrationslogik schreiben.

## Tests

`warden/tests/test_git_state.py`, `test_api_state.py`: zwei Endpoints mit gleichem
Projektpfad → getrennte Zähler; `open_branches(host)`/`open_mrs(host)` zählen nur den
Endpoint; die per-Endpoint-Obergrenze aus `effective_rules` greift (Override höher/niedriger
als Default). `test_git_reconcile.py`/`test_api_reconcile.py`: Reconcile läuft pro Endpoint
und überspringt `closed`. Single-Endpoint-Verhalten unverändert.

## Verifikation

`cd warden && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`

## Commit

Als `EddyXorb`, kein Co-Author-Trailer. Nachricht:

```
refactor(state): per-endpoint quota keyed by (host, project)
```

## Fertig-Kriterium

Reconcile läuft pro Endpoint; Branch-/MR-Zähler und Quotas sind per-Endpoint
(`host`-gefiltert) und nutzen `effective_rules`; kein `implicit_host` mehr im State-Pfad;
Tests grün.

## Status

✅ Erledigt. Umsetzung folgt der 4-Punkte-Liste, plus zwei Zusatzentscheidungen:

- **`writes`-Tabelle bekam ebenfalls eine `host`-Spalte** (Schema v2 → v3):
  `max_writes_per_hour` ist über `effective_rules` genauso per-Endpoint überschreibbar wie
  `max_open_branches`/`max_open_mrs`, also muss auch der Zähler, gegen den geprüft wird
  (`writes_last_hour`), per-Endpoint sein — ein globaler Zähler hätte einem einzelnen
  vielbeschäftigten Endpoint erlaubt, das Rate-Limit aller anderen Endpoints
  mit-aufzubrauchen. Kein Migrationslauf, wie in Punkt 4 beschrieben.
- **`for_each_host_project`'s closed-Host-Toleranz (Commit `32bc43b`) wurde durch einen
  Caller-seitigen Vorfilter ersetzt.** Reconcile ruft die Schleife jetzt mit
  `cfg.open_hosts` auf (neue `Config`-Property, gemeinsam von `reconcile_branches` und
  `reconcile_mrs` genutzt) statt mit `cfg.effective_hosts`; die Schleife selbst vertraut
  darauf, dass jeder übergebene Host offen ist, und lässt `router.for_host` bei einem
  `closed`-Host jetzt ungefangen `KeyError` werfen (Caller-Bug, kein erwarteter Laufzeitfall
  mehr). Verhalten nach außen unverändert — der Regressionstest aus Schritt 03
  (`test_reconcile_completes_and_unlocks_when_one_of_two_endpoints_is_closed`) bleibt grün —,
  aber der Vertrag liegt jetzt beim Aufrufer, nicht mehr in der geteilten Schleife.

Bewusst nicht angefasst: `Config.max_push_bytes` (der Push-Größen-Deckel) wird weiterhin
als globales Feld geprüft (`guards/git/policy.py::decide`), nicht über
`effective_rules(intent.host).max_push_bytes`, obwohl `GitRules` das Feld für die Kaskade
mitführt — ein Endpoint-Override für `max_push_bytes` wird also derzeit still ignoriert.
Das ist kein stateful-Quota-Zähler (Fokus dieses Schritts) und war außerhalb der
4-Punkte-Liste; potenzieller Folge-Fix für einen späteren Schritt.
