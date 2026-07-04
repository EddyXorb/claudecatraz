# 01 — Config-Schema: `[git.rules]` + `[[git.endpoint]]`

**Leitet ab aus** [`../08-multi-target.md`](../08-multi-target.md) §3.1–§3.4. Lies §3
zuerst.

## Ziel

`warden.toml` bekommt die Endpoint-Taxonomie: `[git.rules]` als Domänen-Defaults und
ein `[[git.endpoint]]`-Array, in dem **jeder Eintrag genau einen Host** trägt
(`host`, `type`, `allowed_projects`, optional `rules`). Die alte
`[git.urls] hosts`-Form entfällt ersatzlos. Das ersetzt `_parse_git_url_hosts` und die
`host_order`-Repräsentation in `Config`.

## Umsetzung

Ist-Zustand: `core/config_load.py::_parse_git_url_hosts` liest `[git.urls].hosts` in
`Config.host_order` (tuple); `Config.allowed_hosts`/`implicit_host`/`effective_hosts`
leiten daraus ab.

1. **Datenmodell (`core/config.py`).** Zwei Dataclasses einführen:
   - `GitRules` mit `branch_prefixes`, `max_open_branches`, `max_open_mrs`,
     `max_writes_per_hour`, `max_push_bytes` — jeweils `Optional`, damit „nicht gesetzt"
     unterscheidbar ist (für die Kaskade).
   - `GitEndpoint` mit `host: str`, `type: str`, `allowed_projects: tuple[str, ...]`,
     `rules: GitRules` (leer = keine Overrides).
   `Config` hält `git_rules: GitRules` (Domänen-Defaults) und
   `git_endpoints: tuple[GitEndpoint, ...]`.
2. **Abgeleitete Zugriffe (`core/config.py`).**
   - `allowed_hosts` = `frozenset(normalize_host(e.host) for e in git_endpoints)`.
   - `endpoint_for(host) -> Optional[GitEndpoint]` über `normalize_host`.
   - `effective_rules(host) -> GitRules`: per-Schlüssel-Merge
     `endpoint.rules[k] ?? git_rules[k] ?? built-in default` (Listen werden **ersetzt**,
     nicht gemergt).
   - `project_allowed(host, project)` prüft gegen die `allowed_projects` **des
     Endpoints** (nicht mehr global).
3. **Parser (`core/config_load.py`).** `_parse_git_url_hosts` durch `_parse_git(file)`
   ersetzen, der `[git.rules]` und das `[[git.endpoint]]`-Array liest. `type` gegen die
   Menge implementierter Typen prüfen (`{"gitlab", "github", "plain"}` — `github` ist
   reserviert, bis sein Guard existiert; siehe Nicht tun). `rules` je Endpoint als
   Inline-Tabelle parsen.
4. **Fail-closed-Validierung (§3.4):** unbekannter `type`, doppelter `host`, unbekannter
   Key in einer `rules`-Tabelle, sowie malformte Formen → `ConfigError` (Startabbruch).

## Nicht tun

- Keine Rückwärtskompatibilität zu `[git.urls]` — vollständig entfernen.
- `allowed_projects` **nicht** kaskadieren (kein Domänen-Default-Projektliste); es ist
  immer per-Endpoint.
- `type = "github"` nicht „durchwinken", solange kein GitHub-Guard existiert: als
  bekannter, aber noch-nicht-implementierter Typ behandeln → klarer `ConfigError`
  („type github noch nicht implementiert"), nicht stillschweigend akzeptieren.
- Credentials/Access-Mode hier **nicht** anfassen — das ist Schritt 02.

## Tests

`warden/tests/test_config.py`: `[git.rules]` + mehrere `[[git.endpoint]]` werden geparst;
`effective_rules` liefert Merge (Endpoint-Override gewinnt, Liste ersetzt, fehlender Key
fällt auf Default); `allowed_hosts`/`endpoint_for`/`project_allowed` pro Host; zwei
Endpoints mit gleichem Projektpfad auf verschiedenen Hosts bleiben getrennt.
Fail-closed: doppelter `host`, unbekannter `type`, unbekannter `rules`-Key → `ConfigError`.

## Verifikation

`cd warden && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`

## Commit

Als `EddyXorb`, kein Co-Author-Trailer. Nachricht:

```
feat(config): warden.toml git.endpoint schema + rules cascade
```

## Fertig-Kriterium

`warden.toml` wird über `[git.rules]` + `[[git.endpoint]]` geladen; `Config` trägt
`git_rules`/`git_endpoints`; `effective_rules`/`endpoint_for`/`project_allowed` sind
per-Host; `[git.urls]`/`host_order` sind weg; Fail-closed-Fälle greifen; Tests grün.
