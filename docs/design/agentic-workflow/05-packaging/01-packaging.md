# 01 — Paketierung: `catraz` als installierbares Werkzeug

**Ziel:** `uv tool install .` macht `catraz` global verfügbar; die CLI-Logik liegt in
`src/catraz/`; `entrypoint.py`+`AGENT.md` werden Paket-Assets. **Verhalten unverändert.**
**Voraussetzung:** aktueller Repo-Stand (`./catraz` Single-File, `entrypoint.py`/`AGENT.md`
im Root). **Konventionen:** Python ≥ 3.11, stdlib-only; Tests `uv run --with pytest python -m
pytest tests/ -q`; Commits ohne Trailer.

## Commit 1.1 — Paket-Skelett + Shim + Asset-Cache

**Neu `pyproject.toml`** (Repo-Root):
```toml
[project]
name = "claudecatraz"
version = "0.2.0"
requires-python = ">=3.11"
description = "Sandbox front door for an autonomous Claude Code agent."
dependencies = []

[project.scripts]
catraz = "catraz.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/catraz"]

[tool.hatch.build.targets.wheel.force-include]
"warden" = "catraz/assets/warden"
"forward-proxy" = "catraz/assets/forward-proxy"
```

**Neu `src/catraz/__init__.py`:**
```python
__version__ = "0.2.0"
```

**Neu `src/catraz/paths.py`:**
```python
"""Asset + project-root resolution."""
import importlib.resources as ir
import shutil
from pathlib import Path

from catraz import __version__


def _repo_root() -> Path | None:
    # Zero-install: this file lives at <repo>/src/catraz/paths.py
    here = Path(__file__).resolve()
    cand = here.parents[2]
    return cand if (cand / "pyproject.toml").exists() else None


def asset_root() -> Path:
    """Deterministically extract packaged assets to a versioned cache and return it.
    Build contexts and compose files are read from here, never from the venv/CWD."""
    dst = Path.home() / ".cache" / "catraz" / __version__
    marker = dst / ".extracted"
    if marker.exists():
        return dst
    (dst / "assets").mkdir(parents=True, exist_ok=True)
    pkg_assets = ir.files("catraz") / "assets"
    if pkg_assets.is_dir():  # installed wheel
        with ir.as_file(pkg_assets) as src:
            shutil.copytree(src, dst / "assets", dirs_exist_ok=True)
    else:  # zero-install source tree: assets under src/, contexts at repo root
        repo = _repo_root()
        assert repo, "cannot locate assets"
        shutil.copytree(repo / "src" / "catraz" / "assets", dst / "assets", dirs_exist_ok=True)
        for ctx in ("warden", "forward-proxy"):
            shutil.copytree(repo / ctx, dst / "assets" / ctx, dirs_exist_ok=True)
    marker.write_text("")
    return dst
```

**Neu Root-Shim `catraz` (ersetzt das alte Single-File erst in 1.2 — hier nur anlegen, alt
nicht löschen):** *(siehe 1.2)*

**Tests `tests/cli/test_paths.py`:**
```python
from catraz import paths, __version__

def test_asset_root_extracts(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(paths.Path, "home", lambda: tmp_path)
    root = paths.asset_root()
    assert root == tmp_path / ".cache" / "catraz" / __version__
    assert (root / "assets" / "warden").is_dir()
    assert (root / "assets" / "compose" / "docker-compose.yml").exists()
```
> Dieser Test setzt voraus, dass die Assets schon liegen — wird erst nach 1.3 grün. In 1.1
> nur `test_version` (s. u.) gaten; `test_paths` mit `@pytest.mark.xfail(reason="assets in 1.3")`
> markieren und in 1.3 das xfail entfernen.

```python
def test_version():
    assert __version__ == "0.2.0"
```

`commit: "build(cli): add pyproject, package skeleton, asset cache"`

## Commit 1.2 — CLI-Code in Module verschieben (verbatim), Root-Shim

Den Inhalt des bisherigen `./catraz` **unverändert** auf Module aufteilen. Funktionen
wörtlich übernehmen (gleiche Signaturen/Logik), nur Imports anpassen.

| Funktion(en) aus altem `catraz` | Neues Modul |
| ------------------------------- | ----------- |
| `load_env`, `set_env_values`, `mask` | `src/catraz/envfile.py` |
| `validate_project`, `_resolve_allowed_projects`, `_read_toml_allowed_projects` | `src/catraz/policy.py` |
| `compose`, `compose_ps`, `resolve_service`, `SERVICES` | `src/catraz/compose.py` |
| `Findings`, `which`, alle `check_*`, `run_doctor`, `_doctor_fix`, `_chown_r`, `print_findings`, `OK/WARN/BAD`, `DOCTOR_SECTIONS`, `SECURITY_SECTIONS` | `src/catraz/doctor.py` |
| `find_root`, `_claude_home` | `src/catraz/paths.py` (zu 1.1 hinzufügen) |
| `Out`, Exit-Codes, `CliError`, alle `cmd_*`, `_run_sync`, `_wait_healthy`, `_row_ready`, `_print_urls`, `_tail_audit`, `cmd_version`, Arg-Parser (`build_parser`/`add_global`/`_g`), `main`, `VERSION`→`__version__`, `COMPONENT_VARS`, `AUDIT_URL` | `src/catraz/cli.py` |

- Querimporte explizit (z. B. `from catraz.envfile import load_env`).
- `VERSION = "0.1.0"` ersetzen durch `from catraz import __version__`; `cmd_version` druckt
  `__version__`.
- `find_root` bleibt **unverändert** (sucht `docker-compose.yml` aufwärts) — Umstellung erst
  in Doc 02.

**Root-Shim `catraz`** (überschreibt das alte Single-File):
```python
#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from catraz.cli import main
sys.exit(main())
```
`chmod +x catraz`.

**`tests/cli/test_catraz.py` anpassen:** Den `_load_catraz()`-Pfad-Loader **ersetzen** durch
echte Imports:
```python
from catraz import cli, envfile, policy
# Aufrufe umschreiben: catraz.validate_project -> policy.validate_project,
# catraz.load_env -> envfile.load_env, catraz.set_env_values -> envfile.set_env_values,
# catraz.mask -> envfile.mask, catraz._read_toml_allowed_projects -> policy....
```
Alle 10 bestehenden Tests müssen unverändert grün sein (nur Modulpräfixe ändern).

**`.github/workflows/cli-ci.yml`** `paths:` ergänzen (`src/catraz/**`, `pyproject.toml`); der
Unit-Schritt bleibt `uv run --with pytest python -m pytest tests/cli/ -q`; Smoke `./catraz
--help`/`--version` bleibt.

`commit: "refactor(cli): split catraz into src/catraz package modules"`

## Commit 1.3 — `entrypoint.py` + `AGENT.md` als Assets

Alle Docker-Build-Inputs leben unter `src/catraz/assets/`.

- `git mv entrypoint.py src/catraz/assets/container/entrypoint.py`. Inhalt **unverändert**.
  (`container/` ist **kein** Python-Paket, nur ein Asset-Ordner.)
- `git mv AGENT.md src/catraz/assets/AGENT.md`. Inhalt unverändert.
- **Root `Dockerfile`** COPY-Pfade anpassen (Build-Kontext bleibt in Doc 01 Repo-Root):
  - `COPY entrypoint.py /entrypoint.py` → `COPY src/catraz/assets/container/entrypoint.py /entrypoint.py`
  - `COPY AGENT.md /opt/claude-dev-env/AGENT.md` → `COPY src/catraz/assets/AGENT.md /opt/claude-dev-env/AGENT.md`
- **Root `README.md`** Verweise `entrypoint.py`/`AGENT.md` auf neue Pfade aktualisieren.
- Platzhalter für spätere Docs: `src/catraz/assets/{compose,claude-layer,bases,config}/.gitkeep`.
- `cli._run_sync`: Entrypoint-Pfad auf `<repo>/src/catraz/assets/container/entrypoint.py`
  setzen (in Doc 04 ohnehin überarbeitet).
- `test_paths.py`: xfail entfernen; `assets/warden`-Assertion behalten, die
  `compose/docker-compose.yml`-Assertion erst in Doc 02 hinzufügen.

**Tests `tests/container/test_entrypoint_import.py`:**
```python
import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

def test_entrypoint_imports():
    p = Path(__file__).resolve().parents[2] / "src/catraz/assets/container/entrypoint.py"
    loader = SourceFileLoader("entrypoint", str(p))
    spec = importlib.util.spec_from_loader("entrypoint", loader)
    mod = importlib.util.module_from_spec(spec); loader.exec_module(mod)
    assert hasattr(mod, "cmd_start") and hasattr(mod, "cmd_sync")
```

`commit: "build(image): move entrypoint.py and AGENT.md into package assets"`

## Akzeptanz Doc 01
- `uv run --with pytest python -m pytest tests/ -q` grün.
- `./catraz --version` druckt `catraz 0.2.0`.
- `uv tool install . && catraz --help` funktioniert (manuell verifizieren).
- `python3 -c "from catraz import paths; print(paths.asset_root())"` extrahiert nach
  `~/.cache/catraz/0.2.0/`.
