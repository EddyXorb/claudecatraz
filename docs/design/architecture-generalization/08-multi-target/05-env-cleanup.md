# 05 — `.env`-/Env-Aufräumung (Warden-Paket)

**Leitet ab aus** [`../08-multi-target.md`](../08-multi-target.md) §3.5. Lies §3.5 zuerst.
**Hängt ab von** Schritt 01, 02, 03 (die Ersatzquellen existieren dann).

## Ziel

Eine Einstellung, eine Quelle: der Config-Loader liest **keine** Policy-Env-Overrides und
keine Forge-Identität mehr aus der Umgebung. Policy lebt in `warden.toml`, Secrets in den
Token-Dateien. Dieser Schritt entfernt die alten Env-Eingänge im Warden-Paket; die
Compose-/`.env`-Asset-Seite macht Schritt 07.

## Umsetzung

Ist-Zustand (`core/config_load.py::from_env`): liest `GITLAB_URL` (→ `api_url`),
`GITLAB_MODE` (→ `gitlab_mode`), und Overrides `ALLOWED_PROJECTS`, `BRANCH_PREFIX`,
`MAX_OPEN_MRS`, `MAX_OPEN_BRANCHES`, `MAX_WRITES_PER_HOUR` (überschreiben `warden.toml`).

1. **Overrides entfernen.** Die genannten `*_PROJECTS`/`*_PREFIX`/`MAX_*`-Env-Reads aus
   `from_env` streichen. Die entsprechenden Werte kommen ausschließlich aus `[git.rules]`
   / `[[git.endpoint]]` (Schritt 01). Damit entfällt auch
   `_additional_host_credential_problems`' Override-Bezug und die frühere §3.4-Ambiguität.
2. **`GITLAB_MODE` entfernen.** Reads + `_VALID_MODES` + `gitlab_mode`-Feld weg (Modus
   kommt aus `access_mode`, Schritt 02).
3. **`GITLAB_URL` entfernen.** Kein `api_url`-Bau mehr aus der Env (Basis-URL wird in
   Schritt 03 aus `host`+`type` abgeleitet). Sicherstellen, dass `implicit_host` (das aus
   `api_url` kam) nach Schritt 03 vollständig weg ist.
4. **Was env bleibt:** nur die secret-Datei-Zeiger (`READ_TOKENS_FILE`/`WRITE_TOKENS_FILE`,
   via `_secret`), Betriebs-Pfade (`log_path`, `audit_log_path`, `ADMIN_UDS`) und
   Test-Fallbacks. Das ist Plumbing, keine Policy — nicht anfassen.

## Nicht tun

- Keine „stille" Rückwärtskompatibilität (z.B. `GITLAB_URL` weiter als Fallback lesen).
  Wenn ein alter Env-Wert gesetzt ist, wird er schlicht ignoriert — nicht heimlich
  angewendet.
- Die compose-interne Plumbing-Env (`WARDEN_REST_URL`, `no_proxy`, `ADMIN_UDS`) **nicht**
  hier entfernen (das ist Asset-Seite, Schritt 07) und nicht mit Policy verwechseln.

## Tests

`warden/tests/test_config.py`: gesetzte `GITLAB_URL`/`GITLAB_MODE`/`WARDEN_ALLOWED_PROJECTS`
haben **keine** Wirkung mehr (Wert kommt aus `warden.toml`); leere Endpoint-Liste →
default-deny (nicht allow-all). Falls Tests bisher diese Env-Vars setzen, umstellen auf
`warden.toml`/Token-Dateien.

## Verifikation

`cd warden && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`

## Commit

Als `EddyXorb`, kein Co-Author-Trailer. Nachricht:

```
refactor(config): drop GITLAB_MODE/GITLAB_URL and env policy overrides
```

## Fertig-Kriterium

`from_env` liest keine Policy-Overrides, kein `GITLAB_MODE`, kein `GITLAB_URL` mehr; die
zugehörigen `Config`-Felder sind weg; Policy kommt allein aus `warden.toml`; Tests grün.
