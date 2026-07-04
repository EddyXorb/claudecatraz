# 03 — REST-Guard: per-Host-Tabellen, `enabled_via`/`/policy` per-Host, `[api.endpoints]`-Entfall

**Leitet ab aus** [`../09-endpoint-actions.md`](../09-endpoint-actions.md) §2
(Schichtung, Read-Tabelle bleibt invariant) und §5 (REST-Guard-Bullet +
`[api.endpoints]`-Entfall). Lies §2 zuerst. **Hängt ab von** Schritt 01
(Action-Katalog) und 02 (`effective_actions`).

**Parallel zum anderen Schritt 03** (git-Guard) — beide konsumieren nur `effective_actions` und die
Katalog-Helfer aus 01/02, hängen aber nicht voneinander ab. Getrennte Commits.

## Ziel

`build_effective_table` läuft **pro Endpoint** statt einmal global. Der `ApiGuard`
hält `host → EffectiveTable` und wählt beim Matchen die Tabelle des Ziel-Hosts.
`enabled_via` und der `/policy`-Report werden per-Host. Die globale
`[api.endpoints].enable`-Config wird **ersatzlos** durch die effektiven Actions
ersetzt.

## Ist-Zustand

- `warden/guards/gitlab_api/catalog/activation.py::build_effective_table(cfg, enable)`
  baut **eine** `EffectiveTable` aus `cfg.endpoint_enable`
  (`DEFAULT_ENABLED`-Fallback, FORBIDDEN-Backstop).
- `warden/guards/gitlab_api/guard.py`:
  - `__init__` (Zeile ~67): `self._effective = build_effective_table(cfg, cfg.endpoint_enable)` — **eine** Tabelle.
  - `parse` (Zeile ~141): `match_endpoint(self._effective.entries, method, rest_path)`.
    `intent.host` = `request.headers.get("host")` ist hier verfügbar.
  - `capability_gate`/`decide` (Zeile ~171/174) reichen `self._effective` durch.
  - `_enabled_via` (Zeile ~217) liest `self._effective.enabled_via`.
- `warden/core/config.py::Config.endpoint_enable` und der zugehörige
  `[api.endpoints]`-Parser (`ApiEndpointsConfig`/`parse_api_endpoints` in
  `core/config_load.py`) sind die zu entfernende globale Quelle.

## Umsetzung

1. **`build_effective_table` konsumiert Actions statt `enable`.** Signatur auf die
   effektiven **Actions** eines Endpoints umstellen. Intern: die Action-IDs über den
   Katalog (01) auf ihre Recognizer-IDs auffalten, dann die bestehende Logik
   (Recognizer nachschlagen, FORBIDDEN-Backstop, `enabled_via`) laufen lassen.
   - Nur die **REST**-Actions einer Endpoint-Action-Liste sind hier relevant;
     `git.fetch`/`git.push` gehören zum git-Guard-Schritt (03) und werden hier ignoriert (der Helfer aus
     01 trennt Transport- von Forge-Actions).
   - **FORBIDDEN-Backstop bleibt** wörtlich (§2 Punkt 1): ein Recognizer mit
     FORBIDDEN-Capability → `CatalogConfigError`. Actions können FORBIDDEN nie
     ausdrücken, aber der Backstop bleibt beim Tabellenbau stehen.
   - `enabled_via`: Semantik wie heute, aber pro Endpoint. „default" vs. „config"
     bezieht sich jetzt auf den Built-in-Default (01) vs. eine abweichende
     `actions`-Liste. Präzise Marker-Werte darfst du an den Ist-Code angleichen; die
     Audit-Semantik „nicht-Default-Aktivierung sichtbar" muss erhalten bleiben.
2. **Per-Host-Tabellen im `ApiGuard`.** `self._effective` (eine Tabelle) →
   `self._effective_by_host: Mapping[str, EffectiveTable]`, gebaut **einmal** in
   `__init__` über `cfg.git_endpoints`/`cfg.effective_actions(host)` (billig, N
   Tabellen, kein Rebuild — §4, keine Laufzeit-Reload-Doktrin).
   - Ein kleiner Zugriff `self._table_for(host) -> EffectiveTable`, der über
     `cfg.normalize_host` nachschlägt. Ein Host **ohne** Endpoint hat keine Tabelle —
     das kann hier nur passieren, wenn die Kernel-`host_gate` (08) noch nicht gegriffen
     hat; da `parse` vor dem Kernel-Gate läuft, gib in dem Fall eine **leere** Tabelle
     zurück (matcht nichts → default-deny), nie einen Crash. (Analog zum bestehenden
     `state_view`-Kommentar im git-Guard, das vor `host_gate` läuft.)
3. **Aufrufer umhängen.** `parse`, `capability_gate`, `decide`, `_enabled_via` nehmen
   die Tabelle des `intent.host` (via `_table_for`) statt der globalen. `intent.host`
   ist seit 08-Schritt-03 überall verfügbar.
4. **`/policy`-Report per-Host.** Wo der Report heute die globale Tabelle serialisiert,
   jetzt pro konfiguriertem Host eine Sektion. Finde die Report-Quelle
   (`guards/gitlab_api/catalog/report.py` und/oder der Admin-`/policy`-Handler) und
   gruppiere nach Host. Der CLI-`catraz policy`-Konsument (`src/catraz/policy.py`)
   muss das neue Format lesen können — prüfe und passe an, sonst bricht `catraz policy`.
5. **`[api.endpoints]` ersatzlos entfernen (§5).**
   - `Config.endpoint_enable` streichen; `ApiEndpointsConfig`/`parse_api_endpoints`
     (bzw. wie sie heißen) aus `core/config_load.py` entfernen.
   - Alle Referenzen (Tests, Template, `doctor`) mitziehen. Grep nach
     `endpoint_enable`, `api.endpoints`, `parse_api_endpoints` und räume restlos auf.
   - Das ist „eine Einstellung, eine Quelle" analog 08 §3.5, pre-1.0, **keine**
     Rückwärtskompatibilität.

## Nicht tun

- **Die Read-Tabelle (`read_endpoints.py`) nicht anfassen** (§2 Punkt 4). Sie bleibt
  invariant und nicht action-adressierbar. `git.fetch` gated hier **nichts** — REST-
  Reads laufen weiter über Read-Tabelle + Projekt-Allowlist.
- **Keinen** Laufzeit-Rebuild der Tabellen (§4). Alles in `__init__`, einmalig.
- Den FORBIDDEN-Backstop **nicht** entfernen oder aufweichen — er bleibt als
  compiled-in Absicherung (§2 Punkt 1).
- Keine Wildcard-/Merge-Semantik einführen — Actions kamen schon in 02 als
  geschlossene, komplett-ersetzende Listen an.

## Tests

`warden/tests/` (Guard-/Policy-Tests, z.B. `test_policy.py`,
`test_api_state.py` und die Activation-Tests):
- `build_effective_table` aus einer Action-Liste: `mr.comment` faltet auf
  `{mr.note, mr.discussion, mr.discussion_reply}` auf; eine Action-Liste ohne
  `mr.create` lässt `POST .../merge_requests` **nicht** matchen (default-deny).
- Zwei Hosts, unterschiedliche `actions`: Host A (`mr.create` aktiv) lässt MR-Create
  zu, Host B (nur `mr.comment`) weist MR-Create ab, erlaubt aber Kommentare — **am
  selben Guard**, über `intent.host` getrennt.
- FORBIDDEN-Backstop greift weiterhin (ein hypothetischer FORBIDDEN-Recognizer →
  `CatalogConfigError`).
- Read-Tabelle unverändert: ein GET, der heute erlaubt ist, bleibt erlaubt —
  unabhängig davon, ob `git.fetch` in `actions` steht.
- `[api.endpoints]` ist weg: eine `warden.toml` mit `[api.endpoints]` → ignoriert oder
  (besser, konsistent mit Fail-closed) unbekannte-Sektion-Verhalten wie im
  Ist-Loader; kein `endpoint_enable`-Attribut mehr auf `Config`.
- `/policy`-Report enthält pro Host eine Sektion mit dessen aktiven Actions.

## Verifikation

`cd warden && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`

(Wenn du das `/policy`-Format geändert hast, zusätzlich den CLI-Konsumenten prüfen:
`uv run --with pytest python -m pytest tests/cli/ -q` im Repo-Root.)

## Commit

Als `EddyXorb`, kein Co-Author-Trailer. Nachricht:

```
feat(api-guard): per-host effective tables from actions; drop [api.endpoints]
```

## Fertig-Kriterium

Der `ApiGuard` hält `host → EffectiveTable`, matcht per `intent.host`, `enabled_via`
und `/policy` sind per-Host; `build_effective_table` konsumiert die effektiven
REST-Actions; die Read-Tabelle ist unverändert; `[api.endpoints]`/`endpoint_enable`
sind restlos entfernt; zwei Hosts mit unterschiedlichen `actions` verhalten sich im
Test unterschiedlich; alles grün.
