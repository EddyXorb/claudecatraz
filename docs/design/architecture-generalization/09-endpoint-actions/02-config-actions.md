# 02 — Config: `actions`-Feld, Kaskade, `type`-Schnitt, Validierung

**Leitet ab aus** [`../09-endpoint-actions.md`](../09-endpoint-actions.md) §1
(Sprache/Platzierung/Kaskade), §3 (Validierung) und §5 (Config-Bullet). Lies §1 und
§3 zuerst. **Hängt ab von** Schritt 01 „Action-Katalog" (gültige IDs +
`actions_valid_for_type`).

## Ziel

`warden.toml` bekommt den nackten Listen-Key `actions` auf **zwei** Ebenen:
`[git].actions` (Domänen-Default) und pro `[[git.endpoint]]` (Override). `Config`
kaskadiert sie mit derselben Mechanik wie `effective_rules` und schneidet das
Ergebnis mit dem Endpoint-`type`. Absent (`None`) und explizit leer (`[]`) bleiben
unterscheidbar.

## Ist-Zustand

- `warden/core/config.py`: `GitEndpoint` (`host`, `type`, `allowed_projects`,
  `rules: GitRules`), `Config.git_rules`/`git_endpoints`, die `_cascade`-Hilfsfunktion
  (Zeile ~48) und `effective_rules(host)` (Zeile ~229). `endpoint_for(host)` und
  `_endpoints_by_host` existieren bereits.
- `warden/core/config_load.py` parst `[git.rules]` + `[[git.endpoint]]` inkl. Inline-
  `rules` und Fail-closed-Validierung (unbekannter `type`, doppelter `host`,
  unbekannter `rules`-Key).

## Umsetzung

1. **Datenmodell (`core/config.py`).**
   - `GitEndpoint` bekommt `actions: Optional[tuple[str, ...]] = None`. `None` =
     „kein Override, Domänen-Default gilt"; `()` = „dieser Endpoint darf nichts".
     Die Unterscheidbarkeit ist Pflicht (§5) — nicht auf `()` normalisieren.
   - `Config` bekommt `git_actions: Optional[tuple[str, ...]] = None` (Domänen-Default;
     `None` = Key `[git].actions` fehlt → Built-in-Default gilt).
2. **`effective_actions(host) -> tuple[str, ...]` (`core/config.py`).** Analog
   `effective_rules`, mit **derselben** `_cascade`-Hilfsfunktion:
   - Kaskade: `endpoint.actions` (falls `is not None`), sonst `git_actions`
     (falls `is not None`), sonst der Built-in-Default aus Schritt 01 (Action-Katalog) (§1.4).
     Die Liste **ersetzt komplett** — genau wie `branch_prefixes` (kein add/remove).
   - **Danach** mit `actions_valid_for_type(endpoint.type)` schneiden (§3.2). Der
     Schnitt greift **nur** für den geerbten Domänen-Default/Built-in-Default: ein
     `plain`-Endpoint, der `[git].actions` erbt, bekommt `∩ {git.fetch, git.push}`,
     ohne Fehler.
   - Der Schnitt eines **explizit** am Endpoint gesetzten `actions` mit einer für den
     `type` unmöglichen ID ist **kein** Filter, sondern ein Fehler → siehe Punkt 4
     (der Fehler fällt schon in `config_load` an, nicht hier).
3. **Parser (`core/config_load.py`).**
   - `[git].actions` als Array-of-Strings lesen (fehlt → `git_actions=None`).
   - `actions` je `[[git.endpoint]]` lesen (fehlt → `endpoint.actions=None`; vorhanden,
     auch `[]` → als Tuple übernehmen).
4. **Fail-closed-Validierung (§3.1/§3.2), im Loader beim Bauen der Config:**
   - `actions` ist kein Array von Strings → `ConfigError`.
   - Unbekannte Action-ID (nicht in der Menge aller gültigen IDs aus Schritt 01 (Action-Katalog)),
     egal auf welcher Ebene → `ConfigError` (Typo-Schutz, §3.1).
   - **Explizites** Endpoint-`actions` mit einer für den Endpoint-`type` unmöglichen
     ID (z.B. `mr.create` auf `type="plain"`) → `ConfigError` (§3.2, „immer ein
     Irrtum"). Der geerbte Default löst **nie** diesen Fehler aus — nur die explizite
     Endpoint-Liste.
   - Sammle den Fehlertext im Stil der bestehenden `rules`-Key-Fehler
     (Host nennen, welche ID, welcher `type`).

## Nicht tun

- `actions` **nicht** in `rules` legen (§1.3, §7). Getrennter Key, getrennte
  Validierung.
- **Keine** Header-Form `[git.actions]` unterstützen/erwarten — es ist ein Listen-Key
  (§1.3). (TOML-seitig ist `[git]` mit `actions = [...]` gemeint, nicht ein
  `[git.actions]`-Table.)
- `None` und `()` **nicht** verschmelzen — die Unterscheidung „erbt" vs. „kann nichts"
  ist tragend (§5).
- **Kein** `actions_add`/`actions_remove` (§1.4/§7).
- Den Access-Mode/Token-Kram **nicht** anfassen — Actions verengen nur, die Decke
  bleibt 08 §4.2 (§2). Kein Fehler bei „Write-Action ohne write_token" — das ist eine
  `doctor`-Warnung in Schritt 04.

## Tests

`warden/tests/test_config.py` (erweitern):
- `[git].actions` + Endpoint-`actions` werden geparst; `effective_actions` liefert
  die Kaskade (Endpoint-Override gewinnt und **ersetzt** komplett; fehlender
  Endpoint-Key erbt Domäne; fehlende Domäne fällt auf Built-in-Default).
- `plain`-Endpoint ohne eigenes `actions` erbt `[git].actions` **geschnitten** →
  `{git.fetch, git.push}` (§3.2), ohne Fehler.
- Explizit leeres `actions = []` am Endpoint ⇒ `effective_actions` = leer
  (unterscheidbar von „Key fehlt").
- Built-in-Default greift, wenn **kein** `actions` irgendwo gesetzt ist, und ist
  identisch zur Konstante aus Schritt 01 (Action-Katalog).
- Fail-closed: unbekannte Action-ID (Domäne **und** Endpoint) → `ConfigError`;
  `mr.create` explizit auf `type="plain"` → `ConfigError`; `actions` als Nicht-Liste
  → `ConfigError`.

## Verifikation

`cd warden && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`

## Commit

Als `EddyXorb`, kein Co-Author-Trailer. Nachricht:

```
feat(config): per-endpoint actions with cascade + type-cut
```

## Fertig-Kriterium

`GitEndpoint.actions`/`Config.git_actions` existieren, `effective_actions(host)`
kaskadiert per-Host und schneidet mit `type`, die Fail-closed-Fälle greifen, `None`
vs. `()` bleibt unterscheidbar, Tests grün. Noch **kein** Guard konsumiert
`effective_actions` — das ist 03 (REST-Guard / git-Guard).
