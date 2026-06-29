# 00 — Implementierungsplan: Übersicht & Konventionen

Umsetzung des Repackaging (Design: `../05-repackaging.md`). **Jedes** `NN-*.md` ist
eigenständig umsetzbar — man muss das Hauptdokument nicht lesen. Reihenfolge **strikt**
01 → 06 (jeder Schritt setzt auf dem vorigen auf).

| Doc | Liefert |
| --- | ------- |
| [`01-packaging.md`](./01-packaging.md) | Installierbares Paket (`uv tool install`), `src/catraz/`-Layout, Assets, Asset-Cache, Root-Shim |
| [`02-catraz-home.md`](./02-catraz-home.md) | `.catraz/`-Heim, `find_root` aufwärts, Compose aus Asset, `init`/`migrate`, `.gitignore`, Admin/Audit über Unix-Socket (löst Parallel-Kollision) |
| [`03-shadow-mount.md`](./03-shadow-mount.md) | tmpfs-Shadow `/workspace/.catraz`, Quellpfad-Symlink-Guard, Red-Team T1–T9 |
| [`04-auth-entrypoint.md`](./04-auth-entrypoint.md) | `AUTH_MODE`-XOR, RO-Home-Topologie, Entrypoint-Umbau, `.claude.json`-Provisionierung |
| [`05-image-layering.md`](./05-image-layering.md) | Claude-Layer `FROM ${BASE_IMAGE}`, Default-Base, `BASE_*`-Modi, `doctor base` |
| [`06-local-mode.md`](./06-local-mode.md) | `catraz up` infra-only + `remote`-Profil, `catraz local` drop-in `claude` |

## Konventionen (gelten für alle Docs)

- **Sprache/Tooling:** Python ≥ 3.11, Standardbibliothek only für `catraz` (keine Laufzeit-
  Deps). Build-Backend `hatchling`. Tests: `pytest`.
- **Testlauf:** `uv run --with pytest python -m pytest tests/ -q` (muss grün sein, bevor
  committet wird).
- **Lint:** wo `ruff` schon konfiguriert ist (warden) unverändert lassen; neuer catraz-Code
  hält `line-length=100`.
- **Commits:** Conventional-Commits-Betreff (`feat(...)`, `refactor(...)`, `test(...)`,
  `build(...)`, `docs(...)`). **Keine Trailer** — kein `Co-Authored-By`, keine
  `Claude-Session`-Zeile. Autor = Repo-Eigner. Ein Commit pro „Commit N.x"-Block; der
  Block ist grün-testbar abgeschlossen, bevor committet wird.
- **Branch:** auf dem aktuellen Feature-Branch arbeiten; nicht auf `main` committen.
- **Keine Verhaltensänderung ohne Test:** jeder Commit, der Logik ändert, bringt seinen Test
  mit (im selben Commit).

## Ziel-Repo-Layout (am Ende von Doc 05)

```
claudecatraz/
├── pyproject.toml
├── catraz                       # Root-Shim (zero-install), ruft catraz.cli:main
├── src/catraz/
│   ├── __init__.py              # __version__
│   ├── cli.py                   # argparse + Befehls-Handler + Out
│   ├── envfile.py               # load_env / set_env_values / mask
│   ├── paths.py                 # find_root, Asset-Cache, claude_home
│   ├── policy.py                # validate_project, allowed_projects-Auflösung
│   ├── compose.py               # docker-compose-Aufruf + Invarianten
│   ├── auth.py                  # AUTH_MODE-Logik
│   ├── image.py                 # Base-/Claude-Layer-Build
│   ├── doctor.py                # Findings + Checks
│   └── assets/                  # ALLE Docker-Build-Inputs liegen hier (Build-Kontexte)
│       ├── AGENT.md
│       ├── container/entrypoint.py   # ehem. ./entrypoint.py (kein Python-Import, nur Asset)
│       ├── compose/docker-compose.yml
│       ├── claude-layer/Dockerfile
│       ├── bases/cpp-rust-python/Dockerfile
│       ├── config/{warden.toml,allowlist.txt,squid.conf}
│       ├── warden/              # force-included Build-Kontext (Doc 01)
│       └── forward-proxy/       # force-included Build-Kontext (Doc 01)
├── warden/                      # unverändert (Build-Kontext, ins Wheel via force-include)
├── forward-proxy/               # unverändert (Build-Kontext, ins Wheel via force-include)
└── tests/
    ├── cli/                     # pure-logic unit tests (kein Docker)
    └── redteam/                 # docker-abhängige Negativtests (eigenes CI-Job)
```

## Test-Schichten

- **Unit** (`tests/cli/`, `tests/container/`): reine Logik, **kein** Docker. Gaten jeden
  Commit. Laufen in `cli-ci.yml`.
- **Integration/Red-Team** (`tests/redteam/`): brauchen Docker (Image-Build, Container-Start).
  Eigenes CI-Job mit Docker; lokal manuell. Werden in Doc 03/05/06 ergänzt und sind **nicht**
  Teil des schnellen Unit-Gates, aber Pflicht vor „Schritt fertig".

## Asset-Auflösung (von Doc 01 etabliert, von allen genutzt)

`catraz` liest Assets nie relativ zum CWD, sondern über `paths.asset_root()` →
extrahiert die Paket-Assets deterministisch nach `~/.cache/catraz/<__version__>/assets/` und
gibt `~/.cache/catraz/<__version__>/` zurück. Compose liegt unter
`<asset_root>/assets/compose/docker-compose.yml`; Build-Kontexte sind **relativ zur
Compose-Datei** (`../warden`, `../forward-proxy`, Agent-Kontext `..`). `-f <compose>` und alle
`build.context` zeigen in den Cache, nie ins venv/CWD.
