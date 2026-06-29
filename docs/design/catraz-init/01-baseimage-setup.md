# Baseimage-Konfiguration in `catraz init`

> Status: improved after roast · Topic: catraz-init · Iterations planned: 1

## Goal

Beim Ausführen von `catraz init` soll der Nutzer konfigurieren können, welches Baseimage verwendet wird. Zur Auswahl stehen: (1) das eingebaute Standard-Image, (2) ein fertiges Docker-Image (z.B. `python:3.11`), oder (3) ein Dockerfile-Pfad (z.B. `./Dockerfile`). Die Wahl wird als `BASE_IMAGE` oder `BASE_DOCKERFILE`/`BASE_CONTEXT` in `.catraz/.env` gespeichert.

## Context / constraints

- `src/catraz/image.py` liest `BASE_IMAGE` und `BASE_DOCKERFILE`/`BASE_CONTEXT` bereits aus `.catraz/.env` via `resolve_base()` — **kein Code in `image.py` nötig**.
- `.env.example` dokumentiert beide Optionen bereits als Kommentare — konsistent bleiben.
- `_wizard_interactive.py` enthält alle interaktiven Prompts für `catraz init`.
- `_wizard_yes.py` verarbeitet `--yes` (non-interaktiv): liest Werte aus Umgebungsvariablen/`.env`.
- Tests in `test_init_wizard.py` folgen dem Muster: `_make_root()` + `monkeypatch` + `cmd_init()`.
- Bei `--force=False` und bereits gesetztem Wert soll kein Re-Prompt erfolgen (Konsistenz mit `_prompt_auth_mode`).

## Approach

Nur die Wizard-Schicht berühren. Das Resolver-Backend (`image.py`) ist vollständig — wir müssen nur sicherstellen, dass der Init-Wizard die Werte nach `.env` schreibt.

**Interaktiv:** neue Funktion `_prompt_base_image(env, args, updates, out)` in `_wizard_interactive.py`:
1. Wenn `BASE_IMAGE` oder `BASE_DOCKERFILE` bereits in `env` gesetzt sind und `args.force` nicht, Wert beibehalten + Meldung ausgeben.
2. Sonst: `out.choice()` mit drei Optionen:
   - `0` → bundled (Standardimage) — keine Änderung nötig
   - `1` → fertiges Image — `out.ask("Docker image tag", ...)` → `updates["BASE_IMAGE"] = tag`
   - `2` → Dockerfile — `out.ask("Dockerfile path (relative to project)", "./Dockerfile")` → validieren ob Pfad existiert (Warnung wenn nicht, kein Fehler) → `updates["BASE_DOCKERFILE"] = path`; optional `out.ask("Build context (default: Dockerfile dir)", "")` → wenn gesetzt: `updates["BASE_CONTEXT"] = ctx`.

**`--yes`-Modus:** in `_wizard_yes()` nach `gitlab_url`-Block: Wenn `os.environ.get("BASE_IMAGE")` gesetzt → `updates["BASE_IMAGE"] = value`. Wenn `os.environ.get("BASE_DOCKERFILE")` gesetzt → `updates["BASE_DOCKERFILE"] = value`; wenn dazu auch `BASE_CONTEXT` → `updates["BASE_CONTEXT"] = ctx`.

**Wichtig:** `BASE_IMAGE` und `BASE_DOCKERFILE` sind sich gegenseitig ausschließend — wenn im `--yes`-Modus beide gesetzt sind, hat `BASE_IMAGE` Priorität (konsistent mit `resolve_base()`). Die Exklusivität muss in der **Datei** `.env` erzwungen werden — nicht im `updates`-Dict — weil `set_env_values` nur Keys setzen/auskommentieren kann, aber keine Keys löschen. Dafür ist `unset_env_keys` (bereits importiert) zuständig.

**`--yes` vs. `--force`-Asymmetrie:** Im `--yes`-Modus werden Werte immer aus der Umgebung übernommen (kein `force`-Check) — das ist intentional für CI/Automations-Kontexte, konsistent mit `AUTH_MODE` und `GITLAB_URL` in `_wizard_yes`.

## Steps

### Schritt 1 — `_wizard_interactive.py`: Neue Prompt-Funktion

Datei: `src/catraz/commands/setup/_wizard_interactive.py`

Import ergänzen: `from catraz.envfile import load_env, unset_env_keys` — `unset_env_keys` ist bereits in dem Modul vorhanden (Zeile 6), kein neuer Import nötig.

Füge neue Funktion hinzu:

```python
def _prompt_base_image(
    root: Path,
    env: dict[str, str],
    env_path: Path,
    args: argparse.Namespace,
    updates: dict[str, str],
    out: Out,
) -> None:
    """Ask which base image / Dockerfile to use; write to updates and clean up .env."""
    # Skip re-prompt if already configured and not forced
    if not args.force and (env.get("BASE_IMAGE") or env.get("BASE_DOCKERFILE")):
        existing = env.get("BASE_IMAGE") or env.get("BASE_DOCKERFILE")
        out.info(f"\n  base image already set ({existing}) — keeping. Use --force to change.")
        return

    # out.choice() returns the value string; user types 1/2/3.
    # On StopIteration/empty input it retries 3 times then falls back to default=0 → "bundled".
    choice = out.choice(
        "\nBase image for the container?",
        [
            ("bundled",    "bundled — built-in cpp/rust/python toolchain (default)"),
            ("image",      "custom image — a ready-made Docker image tag"),
            ("dockerfile", "Dockerfile — build from a local Dockerfile"),
        ],
        default=0,
    )
    if choice == "bundled":
        return
    if choice == "image":
        tag = out.ask("Docker image tag (e.g. python:3.11)", env.get("BASE_IMAGE", ""))
        if tag:
            updates["BASE_IMAGE"] = tag
            # Remove stale Dockerfile keys from .env (set_env_values cannot delete keys)
            unset_env_keys(env_path, ["BASE_DOCKERFILE", "BASE_CONTEXT"])
    elif choice == "dockerfile":
        df = out.ask("Dockerfile path (relative to project)", env.get("BASE_DOCKERFILE", "./Dockerfile"))
        if df:
            df_abs = (root / df).resolve()
            if not df_abs.exists():
                out.warn(f"Dockerfile not found at {df_abs} — run `catraz run` after placing it there.")
            updates["BASE_DOCKERFILE"] = df
            # Remove stale BASE_IMAGE key from .env
            unset_env_keys(env_path, ["BASE_IMAGE"])
            ctx = out.ask("Build context directory (Enter for Dockerfile's dir)", env.get("BASE_CONTEXT", ""))
            if ctx:
                updates["BASE_CONTEXT"] = ctx
```

Dann in `_wizard_interactive()` am Ende des Blocks (nach `_prompt_anthropic_key`, vor der Zusammenfassung):

```python
_prompt_base_image(root, env, env_path, args, updates, out)
```

Die Zusammenfassung am Ende soll BASE_IMAGE/BASE_DOCKERFILE ebenfalls anzeigen:

```python
base_part = ""
if updates.get("BASE_IMAGE"):
    base_part = f"  base={updates['BASE_IMAGE']}"
elif updates.get("BASE_DOCKERFILE"):
    base_part = f"  dockerfile={updates['BASE_DOCKERFILE']}"
```

Und in den `out.info()`-Aufruf einfügen.

### Schritt 2 — `_wizard_yes.py`: `--yes`-Modus

Datei: `src/catraz/commands/setup/_wizard_yes.py`

Import ergänzen: `from catraz.envfile import unset_env_keys` am Anfang (zu den bestehenden Imports hinzufügen).

In `_wizard_yes()` Signatur muss `env_path` bereits vorhanden sein (es ist bereits ein Parameter).

Nach dem `gitlab_url`-Block:

```python
base_image = (os.environ.get("BASE_IMAGE", "").strip() or env.get("BASE_IMAGE", "")).strip()
base_dockerfile = (os.environ.get("BASE_DOCKERFILE", "").strip() or env.get("BASE_DOCKERFILE", "")).strip()
base_context = (os.environ.get("BASE_CONTEXT", "").strip() or env.get("BASE_CONTEXT", "")).strip()
if base_image:
    updates["BASE_IMAGE"] = base_image
    unset_env_keys(env_path, ["BASE_DOCKERFILE", "BASE_CONTEXT"])
elif base_dockerfile:
    updates["BASE_DOCKERFILE"] = base_dockerfile
    unset_env_keys(env_path, ["BASE_IMAGE"])
    if base_context:
        updates["BASE_CONTEXT"] = base_context
```

### Schritt 3 — Tests: `tests/cli/test_init_wizard.py`

Neue Klasse `TestBaseImageWizard` anhängen.

**Input/Return-Konvention:** `out.choice()` nimmt Benutzereingabe als String "1"/"2"/"3" entgegen und gibt den String-Wert des gewählten Tupels zurück (`"bundled"`, `"image"`, `"dockerfile"`). Im Test-Stub gibt der `_input`-Mock den Eingabe-String zurück; bei `StopIteration → ""` versucht `out.choice()` 3x und fällt dann auf Default 0 → `"bundled"` zurück. Das ist intentionales Verhalten aller bestehenden Tests, die keine Baseimage-Eingabe liefern.

**Existing tests bleiben kompatibel:** Die bestehenden Test-Stubs (z.B. `iter(["3"])` für GitLab-off) enden mit `StopIteration → ""`. Der neue `_prompt_base_image` kommt danach und bekommt 3x "" → Default "bundled" → return ohne Schreiben. Kein bestehender Test muss geändert werden.

**Interaktiv-Tests (input-Sequenz startet nach allen bisherigen Prompts):**

Für Tests mit `force=False` und `AUTH_MODE` in `.env`: Die bisherigen Prompts sind `gitlab_mode` (1 input). Mit `force=False` wird `_prompt_auth_mode` übersprungen. Dann kommt `_prompt_base_image` als nächster.

- `test_bundled_choice_writes_nothing`:
  - Setup: gitlab_mode=off (`"3"`), dann bundled-choice (`"1"` → `out.choice` erste Eingabe)
  - Erwartung: kein `BASE_IMAGE`, kein `BASE_DOCKERFILE` in `.env`

- `test_custom_image_written_to_env`:
  - inputs: `["3", "2", "python:3.11"]` — GitLab off, dann image-choice, dann tag
  - Erwartung: `env["BASE_IMAGE"] == "python:3.11"`

- `test_dockerfile_written_to_env`:
  - inputs: `["3", "3", "./Dockerfile", ""]` — GitLab off, dockerfile-choice, Pfad, kein Kontext
  - Erwartung: `env["BASE_DOCKERFILE"] == "./Dockerfile"`, kein `BASE_CONTEXT`

- `test_dockerfile_with_context_written`:
  - inputs: `["3", "3", "./Dockerfile", "."]` — wie oben aber mit Kontext
  - Erwartung: `env["BASE_DOCKERFILE"] == "./Dockerfile"`, `env["BASE_CONTEXT"] == "."`

- `test_already_set_not_reprompted`:
  - `.env` enthält bereits `BASE_IMAGE=python:3.11`, `force=False`
  - inputs: `["3"]` — nur GitLab off nötig, kein Baseimage-Prompt
  - Erwartung: `env["BASE_IMAGE"] == "python:3.11"` (unverändert)

- `test_force_reprompts_even_when_set`:
  - `.env` enthält `BASE_IMAGE=old:tag`, `force=True`
  - inputs: `["", "1", "2", "new:tag"]` — auth_mode default, gitlab read-write default, image-choice, neuer tag
  - Plus getpass für zwei Tokens.
  - Erwartung: `env["BASE_IMAGE"] == "new:tag"`

- `test_switching_to_dockerfile_removes_base_image`:
  - `.env` enthält `BASE_IMAGE=old:tag`, `force=True`
  - inputs führen zu dockerfile-choice + Pfad
  - Erwartung: `BASE_DOCKERFILE` in `.env`, `BASE_IMAGE` **nicht** in `.env`

**`--yes`-Tests:**
- `test_yes_base_image_from_env`: `BASE_IMAGE=python:3.11` env var → `BASE_IMAGE=python:3.11` in `.env`
- `test_yes_base_dockerfile_from_env`: `BASE_DOCKERFILE=./Dockerfile` env var → `BASE_DOCKERFILE=./Dockerfile` in `.env`
- `test_yes_base_image_takes_priority_over_dockerfile`: `BASE_IMAGE=img:1` + `BASE_DOCKERFILE=./Dockerfile` → `BASE_IMAGE` in `.env`, `BASE_DOCKERFILE` **nicht** in `.env`

## Success criteria

1. `catraz init` (interaktiv): Wenn Nutzer choice=2 wählt und `python:3.11` eingibt, steht `BASE_IMAGE=python:3.11` in `.catraz/.env`.
2. `catraz init` (interaktiv): Wenn Nutzer choice=3 wählt und `./Dockerfile` eingibt, steht `BASE_DOCKERFILE=./Dockerfile` in `.catraz/.env`.
3. `catraz init --yes` mit `BASE_IMAGE=python:3.11` in der Umgebung: `BASE_IMAGE=python:3.11` in `.catraz/.env`.
4. `catraz init` (interaktiv) ohne Änderung (choice=1/bundled): weder `BASE_IMAGE` noch `BASE_DOCKERFILE` in `.env` gesetzt.
5. Alle bestehenden Tests in `test_init_wizard.py` laufen weiter durch (Rückwärtskompatibilität).
6. Neue Tests in `TestBaseImageWizard` laufen durch.
7. `pytest tests/cli/` läuft grün.

## Risks & open questions

- **`unset_env_keys` und nicht-existierende Keys:** `unset_env_keys` ist lt. Tests idempotent wenn der Key nicht in der Datei steht (kein Fehler). Kein Risiko.
- **`_wizard_yes` Signatur:** Prüfen ob `env_path` bereits im Aufruf von `_wizard_yes` übergeben wird (aus `cmd_init`). Laut `__init__.py` Zeile 128 wird `_wizard_yes(env, env_path, secrets_dir, warden_toml, updates, out)` aufgerufen — `env_path` ist Argument #2. ✓

## Revision history

- v0: initial draft
- v1: Roast-Fixes — (1) `updates.pop()` durch `unset_env_keys(env_path, ...)` ersetzt (echte Mutual-Exclusion in `.env`); (2) Test-Beschreibungen auf korrekten `choice()` Input/Return-Kontrakt ("1"/"2"/"3" → "bundled"/"image"/"dockerfile") präzisiert; (3) `env_path` als Parameter in `_prompt_base_image` hinzugefügt; (4) Bestehende-Test-Kompatibilität explizit begründet (StopIteration→"" → 3x retry → Default bundled); (5) `--yes`/`--force`-Asymmetrie dokumentiert. Abgelehnt: #3 (Zufall-Argument) — StopIteration-Muster ist bewusstes Design; #5 (Dopplung) — zwei Severity-Ebenen sind intentional; #6 (env-Fallback) — konsistent mit bestehendem Muster.
