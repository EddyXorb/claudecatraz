# 09 — Claude-Credential: stille Seed-Auffrischung aus dem Host

**Ziel:** Händisches `catraz sync` reduzieren — durch die *eine* ehrliche Automatik, die
trägt: bei jedem (kalten) Start die Sandbox-Seed-Kopie best-effort aus dem Host
nachziehen. **Kein** Ablauf-Heuristik, **kein** Doctor-Countdown, **kein** Re-Login-Nag.

Bezug: `src/catraz/commands/setup.py::_auto_sync_if_needed` (+ `_run_sync`).

---

## 1. Warum nicht der ursprüngliche `expiresAt`-Plan

Ein erster Entwurf wollte `claudeAiOauth.expiresAt` lesen, anzeigen und den Resync daran
koppeln. Ein adversarialer Review hat die **Prämisse** widerlegt (verifiziert am Code):

- **Falsche Datei.** `doctor.check_auth` liest die **Seed**-Kopie
  (`.catraz/claude/.credentials.json`); der Agent läuft auf der **tmpfs**-Kopie, die
  `claude` in-process über den `refreshToken` erneuert (`entrypoint.py:53–57`). Die
  Seed-`expiresAt` beschreibt **nicht** den Live-Token.
- **Falsches Signal.** `expiresAt` ist der *Access*-Token; relevant ist allein ein neuer
  **`refreshToken`** — der hat kein sichtbares Ablaufdatum. Host/Sandbox-`expiresAt`-
  Vergleich erzeugt sinnlosen Churn *und* verpasste Resyncs.
- **„Host frischer" gilt fast nie.** Sandbox-first: einmal am Host einloggen, dann *im
  Container* arbeiten → Host-Datei eingefroren. Der konditionale Resync feuert nie, der
  Re-Login-`else`-Zweig dafür bei **jedem** kalten Start — Dauer-Fehlalarm, obwohl der
  in-Container-Claude problemlos refresht. Genau das Gegenteil des Ziels.

Konsequenz: `expiresAt`-Detection, Doctor-Anzeige und konditionaler Resync **gestrichen**.

---

## 2. Was bleibt: immer best-effort host→sandbox

Es gibt **kein** zuverlässiges *offline* Signal für „muss neu syncen" — das echte Signal
(„Agent konnte sich nicht authentifizieren") sieht nur der Container zur Laufzeit. Die
beste Annäherung, die eine Host-CLI ehrlich leisten kann: **den Seed so frisch wie den
Host halten**.

**Mechanik:** `_auto_sync_if_needed` synct in `subscription` **immer** (nicht nur wenn die
Datei fehlt) best-effort host→sandbox. Der Host bewegt sich vorwärts, wann immer `claude`
am Host refresht; der Seed ist sonst eingefroren (tmpfs, kein Rückfluss). Wer den Host
gelegentlich nutzt, hält damit den Seed automatisch aktuell — **ohne** manuelles `sync`.

**Eigenschaften:**
- **Einbahn** host→sandbox (Sicherheits-Invariante: der untrusted Agent schreibt nie
  Richtung Host). Jeder neue Schritt ist *lesen* oder *host→sandbox kopieren*.
- **Regrediert nie:** Host ist immer ≥ Seed (Seed friert beim Sync ein, Host nur vorwärts).
- **Kein Nag, kein Churn-Lärm:** Auffrischen einer *vorhandenen* Datei ist still
  (Output unterdrückt); nur das **Fehlen** der Datei meldet/warnt wie bisher.
- **Ehrliche Grenze:** Hilft **nicht**, wenn Host *und* Seed tot sind (lange Pause, Host
  nie genutzt) → interaktives `claude`-Login am Host nötig. Das umgeht kein Tool; wir
  behaupten es nicht. Spiegelt **nicht** den Live-Token des Agents — hält nur den Seed
  so frisch wie den Host.

---

## 3. Änderung (`src/catraz/commands/setup.py`)

### 3.1 `_run_sync` bekommt ein `quiet`

Das Asset-`entrypoint.py sync` druckt „Credentials synced into …" auf stdout. Beim stillen
Auffrischen soll das **nicht** erscheinen → optionales Schlucken via `capture_output`:

```python
def _run_sync(root, out, source=None, force=False, quiet=False):
    ...
    r = subprocess.run(cmd, cwd=root, env=dict(os.environ),
                       capture_output=quiet, text=True)
    if r.returncode != 0:
        raise CliError("credential sync failed", EXIT_GENERAL)
```

`cmd_sync` (explizites `catraz sync`) ruft weiter ohne `quiet` → bleibt gesprächig.

### 3.2 `_auto_sync_if_needed` — immer auffrischen

```python
def _auto_sync_if_needed(root, out):
    """Subscription: keep the sandbox seed credential as fresh as the host (best-effort).

    The host ~/.claude credential advances whenever Claude refreshes it there; the
    sandbox seed is frozen at sync time (in-container home is tmpfs → refreshes never
    flow back). So on every (cold) start we re-copy host→sandbox: a host that's used now
    and then keeps the seed current with no manual `catraz sync`. Strictly one-way; the
    untrusted agent never writes toward the host. Cannot help when BOTH host and seed are
    dead (needs an interactive host `claude` login); does NOT reflect the agent's live
    tmpfs token — only keeps the seed as fresh as the host.
    """
    from catraz.paths import claude_home
    if load_env(root / ".catraz" / ".env").get("AUTH_MODE", "subscription") != "subscription":
        return
    had = (claude_home(root) / ".credentials.json").exists()
    if not had:
        out.info("• subscription credential missing — attempting sync…")
    try:
        _run_sync(root, out, quiet=had)          # refresh of an existing seed is silent
    except CliError as e:
        # Missing seed + failed sync is a real problem (auth fails closed downstream).
        # Refreshing an existing seed is best-effort: a briefly-unreachable host must not
        # nag — the existing seed still works.
        if not had:
            out.warn(str(e) + " — run `catraz sync` once authenticated")
```

Aufrufer unverändert: `cmd_up` (real path, vor dem Security-Preflight) und `_ensure_infra`
(run, kalt). Bei warmer Infra früh-returnt `_ensure_infra` ohnehin — die Auffrischung
greift bei **kaltem** Start, was genügt (der langlebige Daemon refresht in tmpfs selbst).

### 3.3 Doctor unverändert

Die bestehende, **wahre** Zeile bleibt (`doctor.py:241`): „subscription token refreshes are
not persisted across restarts — re-run `catraz sync` if auth breaks". Keine Countdown-Zeile
(die Seed-`expiresAt` wäre irreführend, §1).

---

## 4. Verhalten — vorher/nachher

| Lage | vorher | nachher |
| ---- | ------ | ------- |
| Seed fehlt | Sync, bei Fehler `warn` | **identisch** |
| Seed vorhanden, Host frischer | nichts → manuelles `sync` nötig | **stilles Auto-Resync** |
| Seed vorhanden, Host gleich | nichts | stiller No-op-Copy (idempotent), keine Meldung |
| Seed vorhanden, Host kurz weg | nichts | still, **kein** Nag (Seed bleibt) |
| `AUTH_MODE=api_key` | no-op | **identisch** (no-op) |
| `doctor`/Preflight | wahre Persistenz-Warnung | **identisch** |

Kein erfolgreicher Pfad ändert sein Ergebnis außer dem gewollten stillen Auffrischen.

---

## 5. Tests (`tests/cli/`, kein Docker)

Spy auf `setup._run_sync` (bzw. `setup.subprocess.run`), tmp-Seed schreiben:

1. **Seed fehlt** → `_run_sync` mit `quiet=False` gerufen; `out.info` „missing".
2. **Seed vorhanden** → `_run_sync` mit `quiet=True` gerufen (still).
3. **Seed vorhanden, `_run_sync` wirft** → **kein** `warn`, keine Exception entkommt.
4. **Seed fehlt, `_run_sync` wirft** → `warn` gedruckt.
5. **`AUTH_MODE=api_key`** → `_run_sync` **nicht** gerufen.
6. **`_run_sync(quiet=True)`** → `subprocess.run` mit `capture_output=True`; `quiet=False`
   (Default, via `cmd_sync`) → `capture_output=False`. (Bestehende `test_sync_entry`-Spies
   nehmen `**k` → kompatibel.)

Lauf: `uv run --with pytest python -m pytest tests/cli -q` (volle Suite grün).

## 6. Commits

`feat(cli): refresh the sandbox credential seed from the host on every start` (ein Commit;
Test im selben). Keine Trailer (`00-overview.md`).

## 7. Ausdrücklich kein Ziel

OAuth-Refresh nachbauen · Host-Credential automatisch erneuern · Refresh-Token offline
„verifizieren" · `expiresAt`-Anzeige · Rückkanal Sandbox→Host.
