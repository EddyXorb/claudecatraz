# 02 — Credentials & Access-Mode

**Leitet ab aus** [`../08-multi-target.md`](../08-multi-target.md) §4. Lies §4 zuerst.
**Hängt ab von** Schritt 01 (Endpoint-Liste).

## Ziel

Tokens kommen aus zwei gruppierten Dateien `read_tokens` / `write_tokens`
(`<host> <token>` je Zeile). Der Zugriffsmodus je Endpoint wird aus der **Token-
Präsenz** abgeleitet — es gibt keinen `GITLAB_MODE` mehr. Das ersetzt die
Env-Slug-Auflösung (`_resolve_host_credentials` mit `GITLAB_READ_TOKEN__<SLUG>`,
`_host_slug`) und das Feld `Config.gitlab_mode`.

## Umsetzung

Ist-Zustand: `core/config_load.py::_resolve_host_credentials` baut pro Host ein
`HostCredentials(read_token, write_token)` aus Env-Slug-Variablen; `Config.gitlab_mode`
hält `read-only`/`read-write`/`off`.

1. **Parser der gruppierten Dateien (`core/config_load.py`).** Ein `_parse_token_file`
   liest `<name>_FILE` (Compose-Secret, via bestehendem `_secret`-Muster) und zerlegt
   in `host → token`: Split am **ersten Whitespace**; `#`-Kommentare und Leerzeilen
   überspringen; doppelte Host-Zeile → `ConfigError`. Getrennt für `READ_TOKENS_FILE`
   und `WRITE_TOKENS_FILE`.
2. **`_resolve_host_credentials` neu.** Für jeden `git_endpoint` das Token aus den beiden
   Maps ziehen → `host_credentials[host] = HostCredentials(read_token, write_token)`
   (leerer String, wenn nicht vorhanden). `_host_slug` und die
   `GITLAB_READ_TOKEN__<SLUG>`-Logik entfernen.
3. **Access-Mode ableiten (`core/config.py`).** `access_mode(host) -> Literal["closed",
   "read-only", "read-write"]` gemäß §4.2:
   - kein read-Token → `closed` (auch wenn write vorhanden ist).
   - read, kein write → `read-only`.
   - read + write → `read-write`.
   `Config.gitlab_mode`/`_VALID_MODES` entfernen; wo Code den Modus brauchte, jetzt
   `access_mode(intent.host)` verwenden.
4. **Fail-closed-degrade (§4.2), nicht fail-stop.** Fehlende/inkonsistente Tokens
   brechen den Start **nicht** ab: der betroffene Endpoint ist schlicht `closed`. Ein
   `closed`-Endpoint hat kein nutzbares Credential ⇒ Router liefert keinen Upstream ⇒
   `host_gate` deny't R6 (Mechanik in Schritt 03). Beim Laden eine **Warnung** loggen
   (Modul-Logger aus §07 Punkt 3): „host X closed: kein read-Token" bzw. „host X closed:
   write ohne read — lege einen read-scoped Token in read_tokens an" (Least-Privilege-
   Begründung).

## Nicht tun

- Den Write-Token **nicht** als Read-Fallback verwenden (das ist genau der
  Least-Privilege-Bruch, den „write ohne read → closed" verhindert).
- Tokens **nicht** in Prozess-Env spiegeln — nur über `*_FILE`/`/run/secrets` lesen.
- Kein `GITLAB_MODE` wieder einführen; der Modus ist rein abgeleitet.

## Tests

`warden/tests/test_config.py` (oder `test_credentials.py`): `_parse_token_file` (Split,
Kommentare, doppelter Host → `ConfigError`); `access_mode` deckt alle vier Zeilen der
Mode-Leiter ab (keiner/nur read/read+write/write-ohne-read); `write ohne read` erzeugt
`closed` **plus** geloggte Warnung (`caplog`), aber **keinen** Startabbruch.

## Verifikation

`cd warden && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`

## Commit

Als `EddyXorb`, kein Co-Author-Trailer. Nachricht:

```
feat(config): grouped token files + per-endpoint access mode
```

## Fertig-Kriterium

Tokens werden aus `read_tokens`/`write_tokens` aufgelöst; `access_mode(host)` liefert
`closed`/`read-only`/`read-write` aus der Präsenz; `GITLAB_MODE`/`gitlab_mode`/`_host_slug`
sind weg; ein tokenloser oder write-ohne-read Endpoint startet `closed` mit Warnung,
ohne den Warden zu stoppen; Tests grün.
