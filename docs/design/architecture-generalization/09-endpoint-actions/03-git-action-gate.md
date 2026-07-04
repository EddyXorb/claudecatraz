# 03 — git-Guard: Action-Gate für `git.fetch`/`git.push`

**Leitet ab aus** [`../09-endpoint-actions.md`](../09-endpoint-actions.md) §2
(Schichtung) und §5 (git-Guard-Bullet). **Hängt ab von** Schritt 01 (Action-Katalog)
(Transport-Mapping) und 02 (`effective_actions`).

**Parallel zum anderen Schritt 03** (REST-Guard) — beide konsumieren nur die Katalog-/Config-Helfer,
nicht einander. Getrennte Commits.

## Ziel

Ein kleines Gate analog `host_gate`: es bildet die git-Operation auf ihre Action-ID
ab und **denyt**, wenn die Action nicht in den effektiven Actions des Ziel-Hosts ist.
Der Deny fällt bereits bei `advertise`, damit der git-Client eine saubere
Fehlermeldung bekommt, **bevor** er den Pack schickt — dieselbe Form wie der
bestehende `_writes`-Pfad.

## Ist-Zustand

- `warden/guards/git/guard.py`:
  - `parse` (Zeile ~122) erzeugt `GitIntent` mit `operation ∈
    {advertise, upload-pack, receive-pack}`, `service` (bei advertise) und
    `_host = request.headers.get("host")`.
  - `capability_gate` (Zeile ~170) gibt heute nur für `receive-pack` etwas zurück.
  - `decide` (Zeile ~175): Reads/Push-Discovery → `Decision(True, R1, …)`.
  - Deny-Rendering: `git_reject_response` (`warden/guards/git/errors.py`) rendert die
    Ablehnung sauber über die Side-Band (`! [remote rejected] … (warden: R… …)`).
- `warden/core/guard.py::host_gate` (Zeile ~59) ist die Blaupause: default-deny mit
  Rule-Code `R6` für einen Host außerhalb der Allowlist.
- Schritt 01 liefert den Helfer git-Operation → `git.fetch`/`git.push`.

## Umsetzung

1. **Mapping-Helfer verwenden** (aus 01): `advertise(upload)`/`upload-pack` →
   `git.fetch`; `advertise(receive)`/`receive-pack` → `git.push`. Beachte: `advertise`
   trägt in `GitIntent.service` das *angefragte* Backend (`git-upload-pack` vs.
   `git-receive-pack`), daran hängt fetch vs. push schon in der Discovery-Phase.
2. **Action-Gate im git-Guard.** Baue das Gate analog `host_gate` als kleine reine
   Funktion (z.B. in `warden/guards/git/policy.py` oder einem `action_gate`-Helfer):
   - Bestimme die Action-ID der Operation.
   - Hole `cfg.effective_actions(intent.host)` (per-Host, aus 02).
   - Ist die Action **nicht** enthalten → `Decision(False, R6, f"action {action!r} not enabled for host {host!r}")`
     (R6 = derselbe default-deny-Code wie `host_gate`; wähle den Code am Ist-Stand
     der Rules, falls es dort einen spezifischeren gibt — nimm sonst R6).
   - Sonst `None` (durchlassen).
3. **Verdrahtung — Deny schon bei `advertise`.** Rufe das Gate so, dass es **für alle
   drei Operationen** greift, insbesondere `advertise`. Der natürliche Ort ist
   `capability_gate` (läuft früh, vor der Upstream-Weiterleitung); erweitere es so,
   dass es für `advertise`/`upload-pack`/`receive-pack` das Action-Gate konsultiert.
   - Für `git.push`-Deny muss der Client eine saubere Meldung sehen: prüfe, dass der
     bestehende Deny-Renderpfad (`git_reject_response`) auch für einen
     `advertise(receive)`-Deny die richtige Antwort liefert; falls die Discovery einen
     anderen Renderpfad hat, gib dort einen sauberen HTTP-Fehler zurück (kein Crash,
     kein leerer 200).
4. **Reconcile bleibt unberührt (§4).** Reconcile läuft unabhängig von `actions`
   weiter (nur GETs); eine per Neustart entzogene `git.push`-Action lässt existierende
   Branches/MRs unangetastet — sie sind nur nicht mehr erweiterbar. Stelle sicher,
   dass das Gate **nur** den Agent-Pfad (`handle`) betrifft, nicht den
   Reconcile-Pfad.

## Nicht tun

- **Keinen** neuen Token-/Access-Mode-Check hier — das Gate verengt nur *unterhalb*
  der Token-Decke (§2). Ohne write_token ist `git.push` schon faktisch tot; das ist
  eine `doctor`-Warnung (Schritt 04), kein Fehler hier.
- Das Gate **nicht** erst bei `receive-pack` greifen lassen — der Deny gehört an
  `advertise`, sonst schickt der Client erst den ganzen Pack (§5).
- **Keine** REST-Reads gaten — `git.fetch` betrifft ausschließlich den
  git-Transport-Read, nie die REST-Read-Tabelle (§2 Punkt 4).
- Reconcile **nicht** an `actions` koppeln (§4).

## Tests

`warden/tests/test_git_proxy.py` / `test_git_state.py` / `test_policy.py` (passend):
- Host mit `actions = ["git.fetch"]`: ein `advertise(receive)` (push discovery) und
  ein `receive-pack` werden **denied** (R6), ein `advertise(upload)`/`upload-pack`
  läuft durch.
- Host mit vollem Default: fetch **und** push laufen durch.
- Der push-Deny erscheint bereits in der advertise-Phase mit sauberer Client-Meldung
  (Body/Status prüfen), nicht erst nach dem Pack-Upload.
- Reconcile ist von `actions` unabhängig (ein Host ohne `git.push` reconciled trotzdem
  ganz normal — nur GETs).

## Verifikation

`cd warden && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`

## Commit

Als `EddyXorb`, kein Co-Author-Trailer. Nachricht:

```
feat(git-guard): action gate for git.fetch/git.push per host
```

## Fertig-Kriterium

Der git-Guard denyt fetch/push per-Host anhand `effective_actions`, der Deny fällt
schon bei `advertise` mit sauberer Client-Meldung, Reconcile bleibt entkoppelt, Tests
grün.
