# 06 — CLI: `catraz doctor` / `catraz init`

**Leitet ab aus** [`../08-multi-target.md`](../08-multi-target.md) §6. Lies §6 zuerst.
**Hängt ab von** Schritt 01, 02 (Schema + Token-Format stehen).

## Ziel

`doctor` validiert das Multi-Endpoint-Setup host-seitig und **warnt** (verhindert nie den
Start); `init` scaffoldet die neue Struktur. `doctor` und der Warden wenden dieselben
Regeln an — `doctor` freundlich/erklärend, der Warden fail-closed (§6).

## Umsetzung

Ist-Zustand (`src/catraz/doctor.py`): liest die zwei festen Dateien
`.catraz/secrets/gitlab_read_token`/`gitlab_write_token` (`_read_token`, `REQUIRED`-Liste,
`_read_secret_file`), probet sie (`_probe_gitlab_tokens`) und kennt `GITLAB_MODE`.

1. **Gruppierte Token-Dateien lesen.** `.catraz/secrets/read_tokens`/`write_tokens` zu
   `host → token` parsen (dieselbe Regel wie Warden Schritt 02: Split am ersten Whitespace,
   `#`/Leerzeilen ignorieren). Die alten Einzeldateien-Pfade entfernen.
2. **`warden.toml` lesen** (`[git.rules]` + `[[git.endpoint]]`) und kreuzprüfen —
   **Warnungen**, kein Fehler-Exit:
   - Token für einen Host, der in **keinem** Endpoint steht → Warnung „wahrscheinlich
     Tippfehler; Warden ignoriert ihn".
   - Endpoint-Host **ohne** Token → Warnung „Endpoint läuft closed".
   - Host mit write-, aber ohne read-Token → Warnung mit Least-Privilege-Begründung
     (identischer Text/Grund wie Warden Schritt 02).
3. **Proben pro Host.** `_probe_gitlab_tokens` auf „pro vorhandenem Endpoint-Token" heben
   (Erreichbarkeit/Scope je Host), statt der zwei festen Tokens.
4. **`init` scaffolden.** `read_tokens`/`write_tokens` (leer, mode 0600) anlegen und eine
   `warden.toml`-Vorlage mit `[git.rules]` + einem Beispiel-`[[git.endpoint]]` schreiben;
   das Template kommentiert die implementierten `type`-Werte.

## Nicht tun

- `doctor` **nie** den Warden-Start blockieren lassen (auch nicht bei write-ohne-read) —
  nur warnen; die Durchsetzung ist der Warden (Schritt 02/03).
- Die Regeln nicht subtil anders formulieren als der Warden — sie sind ein Kontrakt (§6);
  gleicher Grund, gleiche Schwelle, damit host- und container-Seite nicht driften.
- Keine Secrets ins Log/`doctor`-Output schreiben (nur Host + Status).

## Tests

`tests/cli/` (doctor): unlisted-host-Token → Warnung, Exit 0; Endpoint ohne Token →
Warnung „closed", Exit 0; write-ohne-read → Warnung, Exit 0; sauberes Setup → keine
Warnung. `init` erzeugt die Dateien mit 0600 und eine parsbare `warden.toml`.

## Verifikation

`uv run --with pytest python -m pytest tests/cli/ tests/container/ -q && uv run mypy`

## Commit

Als `EddyXorb`, kein Co-Author-Trailer. Nachricht:

```
feat(cli): multi-endpoint doctor + init scaffolding
```

## Fertig-Kriterium

`doctor` parst die gruppierten Token-Dateien, prüft sie gegen die Endpoints und **warnt**
(Exit 0) bei allen drei Inkonsistenz-Fällen; `init` scaffoldet die neue Struktur; die alten
Einzeldatei-Pfade sind weg; Tests grün.

## Status

✅ Erledigt. `doctor`s eigene Prüf-/Probe-Logik (`check_tokens`, `_probe_gitlab_tokens`,
`_probe_write_user_read`) ist vollständig auf das neue Modell umgestellt — kein Lesen von
`gitlab_read_token`/`gitlab_write_token` bleibt dort übrig, keine Gnadenfrist nötig, weil
diese Prüfungen kein laufendes Deployment gaten (`doctor` blockiert ohnehin nie).

Additiv geblieben, bewusst: `src/catraz/assets/compose/docker-compose.yml` mountet die
beiden alten Dateien nach wie vor 1:1 als Compose-Secrets für den heutigen
Single-Target-Warden (`GITLAB_READ_TOKEN_FILE`/`GITLAB_WRITE_TOKEN_FILE`); dieser Cutover
ist explizit `07-compose-and-agent-routing.md`s Aufgabe (§4.1), nicht diese hier. Ein
sofortiger, ersatzloser Entzug der beiden Dateien aus `_doctor_fix`/dem Wizard hätte jedes
frische `catraz init` sofort kaputt gemacht (`docker compose up` scheitert ohne die
gemounteten Quelldateien). Deshalb bleiben `doctor.SECRETS` und die
Wizard-Dateien (`commands/setup/_wizard_yes.py`, `_wizard_interactive.py`) unverändert, und
`_doctor_fix` scaffoldet jetzt **zusätzlich** die neuen `read_tokens`/`write_tokens`
(leer, 0600) — genau wie Schritt 01/02 im Warden-Paket ihre alten Pfade additiv neben den
neuen stehen ließen, bis der jeweilige Aufrufer (hier: Compose/Wizard, Schritt 07)
umgehängt ist.
