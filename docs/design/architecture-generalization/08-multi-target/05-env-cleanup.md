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

## Status

✅ Erledigt (this commit). Die wörtliche 4-Punkte-Liste (Overrides raus, `GITLAB_MODE`
raus, `GITLAB_URL`/`implicit_host` bereits seit Schritt 03 weg, Rest-Env-Liste geprüft)
war unvollständig — beim Nachvollziehen im Code fielen zwei notwendige Folgen auf, die die
Liste nicht nennt, deren Nichtbeachtung aber sofort kaputten Code hinterlassen hätte:

- **`core/guard.py` hatte zwei Kernel-Gates, die `cfg.gitlab_mode`/`gitlab_enabled`/
  `writes_enabled` lasen.** `mode_gate_off` ist ersatzlos entfernt — es ist vollständig von
  `host_gate` subsumiert (ein Endpoint ohne jedes Token ist `closed`, und `host_gate`
  verweigert `closed`/unbekannte Hosts bereits per echtem Default-Deny, Schritt 03; ein
  Deployment ganz ohne offene Endpoints verweigert damit bereits alles — das *ist* "off").
  `mode_gate_writes` ist auf pro-Host umgehängt: `cfg.access_mode(intent.host) !=
  "read-write"` verweigert einen Write; `closed` fängt `host_gate` vorher ab (Reihenfolge in
  `kernel_gates` bleibt: `host_gate` → `mode_gate_writes` → `project_gate`).
- **`core/config_load.py::_validate`** verzweigte auf `gitlab_mode` und verlangte
  `cfg.read_token`/`cfg.write_token` (das alte *einzelne* Primär-Tokenpaar, populiert aus den
  nackten `GITLAB_READ_TOKEN`/`GITLAB_WRITE_TOKEN`-Envs) je nach Modus. Grep bestätigte: diese
  beiden Felder wurden seit Schritt 03 nirgends mehr im Laufzeitpfad gelesen — `UpstreamRouter`
  baut ausschließlich aus `Config.git_credentials` (dem gruppierten `read_tokens`/
  `write_tokens`-Mechanismus, Schritt 02). Beide Felder, ihre Env-Reads und die
  Mode-Verzweigung in `_validate` waren reines Vestige. Beides ist jetzt weg; `_validate` prüft
  nur noch bedingungslos Quoten-Positivität und die Branch-Prefix-Namespace-Sanity — kein
  Credential/Allowlist-Erfordernis mehr, ein Deployment ohne jedes `[[git.endpoint]]` bootet
  und verweigert alles über `host_gate` (fail-closed *degrade*, nie fail-stop, Schritt 02).
- **Zwei weitere, von der Vorgabe nicht genannte Fundstellen** hätten mit `gitlab_enabled`
  ebenfalls gebrochen: `guards/git/guard.py::reconcile`/`guards/gitlab_api/guard.py::reconcile`
  hatten je ein `if not self.cfg.gitlab_enabled: ...; return True`, um im `off`-Fall den
  Upstream-Call zu überspringen. Entfernt — `core.transport.for_each_host_project` iteriert
  bereits über `cfg.effective_hosts`, das bei keinem konfigurierten Endpoint leer ist, und
  liefert dann klaglos `ok=True` ohne jeden Upstream-Call zurück (Schritt 03); das
  Kurzschluss-Verhalten war seit Schritt 03 bereits doppelt vorhanden, nur unter einem zweiten
  Namen. Und `__main__.py`s Start-Warnung (`if cfg.gitlab_enabled and not cfg.allowed_projects`)
  ist auf `if cfg.git_endpoints and not cfg.allowed_projects` umgestellt — sie warnt nur noch,
  wenn mindestens ein Host-Endpoint konfiguriert ist, die Allowlist aber leer ist.
- Die `MAX_*`-Wildcard-Vorgabe wurde auf `MAX_PUSH_BYTES` ausgeweitet, auch wenn der
  Ist-Zustand-Absatz nur `MAX_OPEN_MRS`/`MAX_OPEN_BRANCHES`/`MAX_WRITES_PER_HOUR` nannte:
  `MAX_PUSH_BYTES` ist derselbe Policy-Tunable-Typ und fällt unter denselben Wildcard-Wortlaut
  ("`MAX_*`-Env-Reads … streichen") und unter das Ziel "keine Policy-Overrides mehr aus Env".
- Testfolge: `warden/tests/test_config.py` komplett überarbeitet (kein `GITLAB_MODE`, keine
  Env-Override-Tests mehr — stattdessen Tests, dass die alten Env-Vars wirkungslos sind;
  `_MIN`-Konstante entfernt, da `_validate` kein Token/Allowlist-Erfordernis mehr hat). Jede
  Fundstelle, die `Config(..., read_token=..., write_token=...)` als Top-Level-Kwargs benutzt
  hat (⁓10 weitere Testdateien), musste angepasst werden — das Feld existiert nicht mehr,
  `HostCredentials(read_token=..., write_token=...)` (pro Host, unverändert) bleibt der einzige
  Ort dafür. `tests/test_policy.py`/`tests/test_git_proxy.py`s alte
  `off`/`read-only`/`read-write`-Modus-Fixtures sind auf `git_credentials`-basierte
  `closed`/`read-only`/`read-write`-Endpoints umgestellt; die `off`/`closed`-Fälle erwarten
  jetzt `R6` (von `host_gate`) statt `R0` (von `mode_gate_off`) — eine echte
  Verhaltensänderung, kein reines Test-Refactoring.
