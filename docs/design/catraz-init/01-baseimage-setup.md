# Baseimage-Konfiguration in `catraz init`

> Status: draft · Topic: catraz-init · Iterations planned: 1

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

**Wichtig:** `BASE_IMAGE` und `BASE_DOCKERFILE` sind sich gegenseitig ausschließend — wenn im `--yes`-Modus beide gesetzt sind, hat `BASE_IMAGE` Priorität (konsistent mit `resolve_base()`).

## Steps

### Schritt 1 — `_wizard_interactive.py`: Neue Prompt-Funktion

Datei: `src/catraz/commands/setup/_wizard_interactive.py`

Füge hinzu:

```python
def _prompt_base_image(
    root: Path,
    env: dict[str, str],
    args: argparse.Namespace,
    updates: dict[str, str],
    out: Out,
) -> None:
    """Ask which base image / Dockerfile to use; write to updates."""
    # Skip re-prompt if already configured and not forced
    if not args.force and (env.get("BASE_IMAGE") or env.get("BASE_DOCKERFILE")):
        existing = env.get("BASE_IMAGE") or env.get("BASE_DOCKERFILE")
        out.info(f"\n  base image already set ({existing!r}) — keeping. Use --force to change.")
        return

    choice = out.choice(
        "\nBase image for the container?",
        [
            ("bundled", "bundled — built-in cpp/rust/python toolchain (default)"),
            ("image",   "custom image — a ready-made Docker image tag"),
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
            # Clear Dockerfile option in case it was set before
            updates.pop("BASE_DOCKERFILE", None)
            updates.pop("BASE_CONTEXT", None)
    elif choice == "dockerfile":
        df = out.ask("Dockerfile path (relative to project)", env.get("BASE_DOCKERFILE", "./Dockerfile"))
        if df:
            df_abs = (root / df).resolve()
            if not df_abs.exists():
                out.warn(f"Dockerfile not found at {df_abs} — run `catraz run` after placing it there.")
            updates["BASE_DOCKERFILE"] = df
            updates.pop("BASE_IMAGE", None)
            ctx = out.ask("Build context directory (Enter for Dockerfile's dir)", env.get("BASE_CONTEXT", ""))
            if ctx:
                updates["BASE_CONTEXT"] = ctx
```

Dann in `_wizard_interactive()` am Ende des Blocks (nach `_prompt_anthropic_key`, vor der Zusammenfassung):

```python
_prompt_base_image(root, env, args, updates, out)
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

In `_wizard_yes()` nach dem `gitlab_url`-Block:

```python
base_image = os.environ.get("BASE_IMAGE", "").strip() or env.get("BASE_IMAGE", "")
base_dockerfile = os.environ.get("BASE_DOCKERFILE", "").strip() or env.get("BASE_DOCKERFILE", "")
base_context = os.environ.get("BASE_CONTEXT", "").strip() or env.get("BASE_CONTEXT", "")
if base_image:
    updates["BASE_IMAGE"] = base_image
elif base_dockerfile:
    updates["BASE_DOCKERFILE"] = base_dockerfile
    if base_context:
        updates["BASE_CONTEXT"] = base_context
```

### Schritt 3 — Tests: `tests/cli/test_init_wizard.py`

Neue Klasse `TestBaseImageWizard` anhängen:

**Interaktiv-Tests:**
- `test_bundled_choice_writes_nothing`: choice=1 (bundled) → weder `BASE_IMAGE` noch `BASE_DOCKERFILE` in `.env`
- `test_custom_image_written_to_env`: choice=2 + tag `python:3.11` → `BASE_IMAGE=python:3.11` in `.env`
- `test_dockerfile_written_to_env`: choice=3 + Pfad `./Dockerfile` → `BASE_DOCKERFILE=./Dockerfile` in `.env`
- `test_dockerfile_with_context_written`: choice=3 + Pfad `./Dockerfile` + Kontext `.` → `BASE_DOCKERFILE` + `BASE_CONTEXT` in `.env`
- `test_already_set_not_reprompted`: `BASE_IMAGE` bereits in `.env`, `force=False` → kein zusätzlicher Input benötigt

**`--yes`-Tests:**
- `test_yes_base_image_from_env`: `BASE_IMAGE` env var → `BASE_IMAGE` in `.env`
- `test_yes_base_dockerfile_from_env`: `BASE_DOCKERFILE` env var → `BASE_DOCKERFILE` in `.env`
- `test_yes_base_image_takes_priority_over_dockerfile`: beide env vars → `BASE_IMAGE` gewinnt

**Input-Sequenz-Hinweis für Tests:** Der neue `_prompt_base_image`-Aufruf passiert am Ende von `_wizard_interactive`, nach dem `api_key`-Block. Die Tests mit `force=False` und vorhandener `AUTH_MODE` in `.env` bekommen als letzte Inputs die Baseimage-Choice.

## Success criteria

1. `catraz init` (interaktiv): Wenn Nutzer choice=2 wählt und `python:3.11` eingibt, steht `BASE_IMAGE=python:3.11` in `.catraz/.env`.
2. `catraz init` (interaktiv): Wenn Nutzer choice=3 wählt und `./Dockerfile` eingibt, steht `BASE_DOCKERFILE=./Dockerfile` in `.catraz/.env`.
3. `catraz init --yes` mit `BASE_IMAGE=python:3.11` in der Umgebung: `BASE_IMAGE=python:3.11` in `.catraz/.env`.
4. `catraz init` (interaktiv) ohne Änderung (choice=1/bundled): weder `BASE_IMAGE` noch `BASE_DOCKERFILE` in `.env` gesetzt.
5. Alle bestehenden Tests in `test_init_wizard.py` laufen weiter durch (Rückwärtskompatibilität).
6. Neue Tests in `TestBaseImageWizard` laufen durch.
7. `pytest tests/cli/` läuft grün.

## Risks & open questions

- **Input-Sequenz der Tests:** Der neue Prompt kommt nach dem Anthropic-API-Key-Prompt. Bestehende Tests müssen eventuell `StopIteration → ""` am Ende sicherstellen (was der `_input`-Stub bereits tut). Prüfen ob alle bestehenden Tests am Ende auf leere Inputs zurückfallen — wenn ja, kein Anpassen nötig.
- **`env` vs. `updates` beim Reprompt-Check:** Die `env`-Dict enthält nur den Zustand vor dem Init. Bei `force=True` wird neu gefragt; bei `force=False` und Wert in `env` wird übersprungen. Das ist konsistent mit `_prompt_auth_mode`.

## Revision history

- v0: initial draft
