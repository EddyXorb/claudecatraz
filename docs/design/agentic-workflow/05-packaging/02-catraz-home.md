# 02 — `.catraz/`-Heim & Compose aus dem Asset

**Ziel:** Laufzeit/Config liegen unter `<projekt>/.catraz/`; `catraz` findet das Projekt wie
`git` aufwärts; Compose kommt aus dem Asset, nicht aus dem CWD. **Voraussetzung:** Doc 01
fertig (Paket, `paths.asset_root()`, Assets unter `src/catraz/assets/`). **Konventionen:**
Python ≥ 3.11 stdlib-only; Tests `uv run --with pytest python -m pytest tests/ -q`; Commits
ohne Trailer.

`.catraz/`-Layout (von `init` erzeugt):
```
<projekt>/.catraz/
├── .env                 # Secrets + DEV_UID + Versionen (gitignored)
├── config/{warden.toml,allowlist.txt,squid.conf}   # editierbare Kopien, ro gemountet
├── claude/              # Claude-Home-Quellen
├── state/warden/        # SQLite
└── logs/{warden,squid}/ # Audit
```

## Commit 2.1 — Deployment-Dateien ins Asset; Compose-Aufruf aus dem Cache

- `git mv docker-compose.yml src/catraz/assets/compose/docker-compose.yml`
- `git mv Dockerfile src/catraz/assets/Dockerfile`
- `git mv config/warden.toml config/allowlist.txt config/squid.conf src/catraz/assets/config/`
  (Root-`config/` danach leer → entfernen; `config/README.md` mit nach `assets/config/`.)
- `git mv .env.example src/catraz/assets/.env.example`
- **Asset-`docker-compose.yml` umschreiben** (Pfade relativ zur neuen Compose-Lage):
  - `build.context` je Service: Agent `context: ..` + `dockerfile: Dockerfile`;
    `warden` `context: ../warden`; `forward-proxy` `context: ../forward-proxy`.
  - **Alle `container_name:`-Zeilen entfernen** (Projektname kommt aus `--project-directory`;
    nötig für parallele Sandboxes).
  - Agent-`volumes`:
    ```yaml
    - ${PROJECT_DIR}:/workspace
    - ${PROJECT_DIR}/.catraz/claude:/home/dev/.claude
    ```
    (Shadow-Mount + RO-Home folgen in Doc 03/04.) `CLAUDE_HOME`/`PROJECT_DIR`-Defaults
    (`~/.claude`, `./workspace`) entfernen — `PROJECT_DIR` ist jetzt Pflicht (catraz setzt es).
  - `warden`-`volumes`:
    ```yaml
    - ${PROJECT_DIR}/.catraz/state/warden:/var/lib/warden
    - ${PROJECT_DIR}/.catraz/logs/warden:/var/log/warden
    - ${PROJECT_DIR}/.catraz/config/warden.toml:/etc/warden/warden.toml:ro
    ```
  - `forward-proxy`-`volumes`:
    ```yaml
    - ${PROJECT_DIR}/.catraz/config/squid.conf:/etc/squid/squid.conf:ro
    - ${PROJECT_DIR}/.catraz/config/allowlist.txt:/etc/squid/allowlist.txt:ro
    - ${PROJECT_DIR}/.catraz/logs/squid:/var/log/squid
    ```
  - `Dockerfile`-COPY-Pfade anpassen (Kontext ist jetzt `assets/`): `COPY container/entrypoint.py
    /entrypoint.py`, `COPY AGENT.md /opt/claude-dev-env/AGENT.md`.
  - **`src/catraz/assets/.dockerignore`** anlegen, das den Agent-Build verschlankt:
    ```
    compose/
    config/
    warden/
    forward-proxy/
    claude-layer/
    bases/
    ```
- **`src/catraz/compose.py`** — Compose-Aufruf vereinheitlichen:
  ```python
  import os, subprocess
  from pathlib import Path
  from catraz.paths import asset_root

  def base_cmd(root: Path) -> list[str]:
      ar = asset_root()
      cmd = ["docker", "compose",
             "-f", str(ar / "assets/compose/docker-compose.yml"),
             "--project-directory", str(root),
             "--env-file", str(root / ".catraz/.env")]
      override = root / ".catraz/compose.override.yml"
      if override.exists():
          cmd += ["-f", str(override)]
      return cmd

  def run(root: Path, args, capture=False, check=True, print_only=False):
      cmd = [*base_cmd(root), *args]
      if print_only:
          print(" ".join(cmd)); return None
      env = dict(os.environ, PROJECT_DIR=str(root))
      try:
          return subprocess.run(cmd, env=env, check=check, capture_output=capture, text=True)
      except FileNotFoundError:
          raise CliError("`docker` not found on PATH", EXIT_DOCKER)
  ```
  Das alte `compose()`/`compose_ps()` aus Doc 01 auf `run`/`base_cmd` umstellen
  (`compose_ps` ruft `run(root, ["ps","--format","json"], capture=True, check=False)`).
  `resolve_service`/`SERVICES` bleiben.
- Workflow `.github/workflows/compose-validate.yml`: Pfad auf
  `src/catraz/assets/compose/docker-compose.yml` + `--project-directory` mit einem Test-`.env`
  anpassen (oder `docker compose -f <asset> config` mit gesetztem `PROJECT_DIR`).

**Tests `tests/cli/test_compose.py`:**
```python
from pathlib import Path
from catraz import compose

def test_base_cmd_points_at_asset_and_project(tmp_path, monkeypatch):
    (tmp_path / ".catraz").mkdir()
    cmd = compose.base_cmd(tmp_path)
    assert "--project-directory" in cmd and str(tmp_path) in cmd
    assert cmd[cmd.index("-f") + 1].endswith("assets/compose/docker-compose.yml")

def test_base_cmd_includes_override_when_present(tmp_path):
    (tmp_path / ".catraz").mkdir()
    (tmp_path / ".catraz/compose.override.yml").write_text("services: {}\n")
    assert str(tmp_path / ".catraz/compose.override.yml") in compose.base_cmd(tmp_path)
```

`commit: "refactor(compose): move deployment files to assets, invoke compose from cache"`

## Commit 2.2 — `find_root` auf `.catraz`, Pfade umstellen, nested-Guard

**`src/catraz/paths.py`** ergänzen:
```python
import os
from catraz.cli import CliError, EXIT_CONFIG   # or move CliError to a small errors.py to avoid cycle

def find_root(explicit: str | None = None) -> Path:
    if explicit:
        root = Path(explicit).resolve()
        if not (root / ".catraz").is_dir():
            raise CliError(f"no .catraz in {root}", EXIT_CONFIG)
        _assert_no_nested(root); return root
    here = Path.cwd().resolve()
    for d in (here, *here.parents):
        if (d / ".catraz").is_dir():
            _assert_no_nested(d); return d
    raise CliError("no .catraz found (run `catraz init`)", EXIT_CONFIG)

def _assert_no_nested(root: Path) -> None:
    top = root / ".catraz"
    for dirpath, dirnames, _ in os.walk(root):
        if ".catraz" in dirnames and Path(dirpath) / ".catraz" != top:
            raise CliError(f"nested .catraz at {Path(dirpath)/'.catraz'} — refuse", EXIT_CONFIG)
        dirnames[:] = [d for d in dirnames
                       if Path(dirpath) / d not in (top, root / ".git")]

def claude_home(root: Path) -> Path:
    return root / ".catraz" / "claude"
```
> **Zyklus vermeiden:** `CliError`/Exit-Codes aus `cli.py` in neues `src/catraz/errors.py`
> ziehen (nur `CliError`, `EXIT_*`); `cli.py`, `paths.py`, `compose.py`, `doctor.py`
> importieren aus `errors`. Reiner Move, kein Verhaltenswechsel.

- `cli.find_root` und `cli._claude_home` entfernen; überall `paths.find_root` /
  `paths.claude_home(root)` verwenden.
- `doctor.py`: Schreib-Dirs-Checks auf `root/".catraz"/...` umstellen (`state`→
  `.catraz/state`, `logs`→`.catraz/logs`, `claude`→`.catraz/claude`). `.env`-Pfad →
  `root/".catraz"/".env"`. `_resolve_allowed_projects` liest `root/".catraz"/"config"/"warden.toml"`.
- `_doctor_fix`/`setup-dirs`-Logik: Dirs unter `.catraz/` anlegen + `chown` auf `DEV_UID`.

**Tests `tests/cli/test_paths.py`** ergänzen:
```python
def test_find_root_walks_up(tmp_path, monkeypatch):
    (tmp_path / ".catraz").mkdir(); sub = tmp_path / "a" / "b"; sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    from catraz import paths
    assert paths.find_root() == tmp_path

def test_nested_catraz_refused(tmp_path):
    (tmp_path / ".catraz").mkdir()
    (tmp_path / "inner").mkdir(); (tmp_path / "inner" / ".catraz").mkdir()
    from catraz import paths, errors
    import pytest
    with pytest.raises(errors.CliError):
        paths.find_root(str(tmp_path))
```

`commit: "feat(cli): resolve project via .catraz upward walk with nested guard"`

## Commit 2.3 — `init` legt `.catraz/` an; `migrate`; `.gitignore`

**`cli.cmd_init`** umschreiben (Wizard-Prompts/Validierung aus Doc 01 behalten, nur Ziele
ändern):
1. `<root>/.catraz/` + Unterordner (`config state/warden logs/warden logs/squid claude`)
   anlegen, `chown` `DEV_UID`.
2. `config/`-Vorlagen aus `asset_root()/assets/config/*` nach `.catraz/config/` kopieren
   (nur falls nicht vorhanden).
3. `.catraz/.env` aus `asset_root()/assets/.env.example` seeden (falls fehlt).
4. Secrets + `WARDEN_ALLOWED_PROJECTS` wie bisher abfragen → `.catraz/.env`.
5. `.gitignore`-Eintrag: Zeile `.catraz/` an `<root>/.gitignore` anhängen (anlegen falls
   fehlt), nur wenn nicht schon vorhanden.
6. Abschluss-`doctor`.

`find_root` ist hier noch nicht anwendbar (das `.catraz` entsteht erst) → `cmd_init`
arbeitet auf `Path(args.dir or Path.cwd())` als root.

**Neuer Befehl `catraz migrate`** (`cli.cmd_migrate`):
```python
def cmd_migrate(root, args, out):
    cat = root / ".catraz"; cat.mkdir(exist_ok=True)
    moves = {"config":"config","state":"state","logs":"logs","claude":"claude",".env":".env"}
    for src_name, dst_name in moves.items():
        src = root / src_name; dst = cat / dst_name
        if src.exists() and not dst.exists():
            src.rename(dst)               # atomic move, same filesystem
    # fail-closed: kein Alt-Layout-Secret darf unter root verbleiben
    leftovers = [n for n in ("claude", "state", ".env") if (root / n).exists()]
    if leftovers:
        raise CliError(f"migration incomplete, still under project root: {leftovers}", EXIT_CONFIG)
    _ensure_gitignore(root)
    out.info(out.green("migrated to .catraz/"))
    return EXIT_OK
```
Parser-Eintrag analog zu den anderen Subcommands; in `main` dispatchen.

**Präzedenz:** existieren `.catraz/` **und** Alt-Layout (`./claude` o. ä.), bricht jeder
mutierende Befehl (`up`/`doctor`) mit `CliError("legacy layout next to .catraz — remove it
or run `catraz migrate`")` ab. Prüfung in `find_root` nach dem `.catraz`-Fund ergänzen.

**Tests `tests/cli/test_init_migrate.py`:**
```python
def test_migrate_moves_and_gitignores(tmp_path):
    (tmp_path / "claude").mkdir(); (tmp_path / ".env").write_text("DEV_UID=1000\n")
    from catraz import cli
    rc = cli.cmd_migrate(tmp_path, None, cli.Out(color=False))
    assert rc == 0
    assert (tmp_path / ".catraz/claude").is_dir() and (tmp_path / ".catraz/.env").exists()
    assert not (tmp_path / "claude").exists()
    assert ".catraz/" in (tmp_path / ".gitignore").read_text()
```

`commit: "feat(cli): init creates .catraz home; add migrate and gitignore handling"`

## Akzeptanz Doc 02
- Unit-Tests grün.
- In leerem tmp-Repo: `catraz -C <dir> init` (mit `-y`/env) erzeugt vollständiges `.catraz/`.
- `catraz -C <dir> up --print` zeigt `docker compose -f …/assets/compose/docker-compose.yml
  --project-directory <dir> --env-file <dir>/.catraz/.env up -d`.
- **Bekannte, später adressierte Grenze:** der hartkodierte `admin-net 172.31.0.2` kollidiert
  bei *parallelen* Sandboxes — als Issue notieren, nicht hier lösen.
