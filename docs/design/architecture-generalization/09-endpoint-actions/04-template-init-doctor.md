# 04 — Template/`init` + `doctor`-Kreuz-Checks

**Leitet ab aus** [`../09-endpoint-actions.md`](../09-endpoint-actions.md) §4
(Wechselwirkungen & `doctor`) und §5 (Template/`init`/`doctor`-Bullet). Lies §4
zuerst. **Hängt ab von** Schritt 01 (Action-Katalog), 02 (Config) und beide Schritte 03 (Guards).

## Ziel

Das von `catraz init` gescaffoldete `warden.toml` setzt den Default **explizit** als
`[git] actions = [...]` mit dem Vokabular als Kommentar. `catraz doctor` bekommt die
Kohärenz-Kreuz-Checks aus §4 — **pro Host**, als **Warnungen** (freundlich/erklärend),
nie als Fehler.

## Ist-Zustand

- `catraz init`-Scaffolding und die Template-`warden.toml` liegen unter
  `src/catraz/commands/setup/` (`__init__.py::cmd_doctor`, plus `_sync.py`,
  `_wizard_interactive.py`, `_from.py`). Finde die Stelle, die das `warden.toml`-
  Template erzeugt (Grep nach `[git.rules]`/`[[git.endpoint]]`/`warden.toml`).
- `src/catraz/doctor.py` (`Findings` mit `f.warn(section, msg, hint)`, `OK/WARN/BAD`)
  prüft heute u.a. Tokens/Endpoints und **warnt** (z.B. „endpoint … has no token — it
  will run closed" bei ~Zeile 318). Das ist die Blaupause für die neuen Warnungen.
- `[api.endpoints]` ist nach dem REST-Guard-Schritt (03) aus Config **und** aus dem Template entfernt —
  falls im Template noch ein Rest steht, hier restlos raus.

## Umsetzung

1. **Template/`init` (§5 Template-Bullet).**
   - Das gescaffoldete `warden.toml` setzt `[git] actions = [...]` **explizit** mit
     dem vollen Built-in-Default:
     `["git.fetch", "git.push", "mr.create", "mr.comment", "mr.update", "pipeline.trigger"]`.
   - Direkt darüber/daneben das Vokabular als Kommentar (Tabelle §1.2: welche Action
     was abdeckt, inkl. der zwei nicht-Default-Actions `branch.create`/`issue.create`).
   - Der Built-in-Default existiert **zusätzlich** im Code (Schritt 01) — der Template-Key ist
     Doku/Startpunkt, **nicht** die Quelle der Wahrheit. „Fehlender Key ≠ leere Liste"
     bleibt wahr; wer den Key löscht, bekommt den Code-Default, nicht „nichts".
   - Ein Review-only-Endpoint als **auskommentiertes** Beispiel (`actions =
     ["git.fetch", "mr.comment"]`, §6) ist wünschenswert, damit der Override-Fall
     sichtbar ist. Verwende **keine** `[git.endpoint.rules]`-Header-Form im Template —
     nur Inline-`rules = { … }` (§1.3, Umsortier-Footgun).
2. **`doctor`-Kreuz-Checks, pro Host (§4).** Baue die effektiven Actions pro Host
   (dieselbe Kaskade wie der Warden; nutze `cfg.effective_actions(host)`, damit
   `doctor` und Warden garantiert übereinstimmen) und warne:
   - **Write-Actions konfiguriert, aber kein write_token für den Host** → Endpoint
     faktisch read-only; die Warnung nennt den Fix (write_token für den Host setzen).
     „Write-Action" = jede Action, die auf einen schreibenden Recognizer/Transport
     abbildet (`git.push`, `mr.*`, `pipeline.trigger`, `branch.create`, `issue.create`;
     `git.fetch` ist read).
   - **`mr.create` ohne `git.push`** → der Source-Branch kann nie entstehen; Warnung.
   - **`pipeline.trigger` ohne `git.push`** → analog; Warnung.
   - **Tote Quotas** (`max_open_mrs` gesetzt, aber `mr.create` fehlt usw.) sind
     **harmlos** und **keine** Warnung wert (§4) — der Zähler wird schlicht nie
     erreicht. Explizit **nicht** warnen.
3. **`doctor`-Textstil** wie in 08 §6 / Ist-`doctor`: erklärend, mit `hint`, immer
   `WARN` (nie `BAD`) — Kohärenzprobleme zwischen Actions sind **keine**
   Sicherheitsprobleme (§4). Der Warden failt nicht; `doctor` warnt.

## Nicht tun

- **Keine** `[git.endpoint.rules]`-Header im Template (§1.3/§7) — Umsortier-Footgun.
- **Keine** Header-Form `[git.actions]` — Listen-Key (§1.3).
- `doctor` **nicht** failen lassen (kein `BAD`, kein Exit≠0) für Action-Kohärenz —
  nur `WARN` (§4).
- **Nicht** vor toten Quotas warnen (§4) — bewusst still.
- Das Template **nicht** als alleinige Default-Quelle behandeln — der Code-Default
  (Schritt 01) bleibt maßgeblich; fehlender Key ≠ leere Liste (§5).

## Tests

`tests/cli/` (Doctor-/Init-Tests):
- `catraz init` erzeugt ein `warden.toml` mit `[git] actions = [...]` (voller Default)
  und dem Vokabular-Kommentar; kein `[api.endpoints]` mehr.
- `doctor` warnt bei: Write-Action ohne write_token; `mr.create` ohne `git.push`;
  `pipeline.trigger` ohne `git.push`.
- `doctor` warnt **nicht** bei einer toten Quota (`max_open_mrs` ohne `mr.create`).
- `doctor` failt in keinem dieser Fälle (Exit-Code bleibt „ok/warn", nicht „bad").

## Verifikation

`uv run --with pytest python -m pytest tests/cli/ -q && uv run mypy`

## Commit

Als `EddyXorb`, kein Co-Author-Trailer. Nachricht:

```
feat(cli): scaffold [git] actions + doctor action cross-checks
```

## Fertig-Kriterium

`catraz init` scaffolded `[git] actions` explizit mit Vokabular-Kommentar (kein
`[api.endpoints]`); `doctor` warnt pro Host bei den §4-Wechselwirkungen und schweigt
bei toten Quotas, ohne je zu failen; Tests grün.
