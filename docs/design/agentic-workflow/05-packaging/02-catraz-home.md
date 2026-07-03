# 02 — `.catraz/`-Heim & Compose aus dem Asset

> **Update (§06-migration.md Schritt 7, Agent-Layer):** die Dockerfile-COPY-Pfade
> unten (`COPY container/entrypoint.py /entrypoint.py`, `COPY AGENT.md
> /opt/claude-dev-env/AGENT.md`) beschreiben den vor-Schritt-7-Stand. Seit Schritt 7
> COPYt `assets/agents/claude/layer.Dockerfile` zusätzlich `agent_contract.py`/
> `git_routing.py` (generisch) sowie `agents/claude/{adapter.py,agent.toml,
> AGENT.md.tmpl}` (agent-spezifisch) flach neben den Entrypoint — es gibt kein
> statisches `AGENT.md`-Bind-Mount mehr, die Instruktionsdatei wird zur Laufzeit
> aus dem Template gerendert (`adapter.render_instructions`, §05.2).
> `paths.claude_home` ist unverändert.

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
├── run/warden/          # Admin-Unix-Socket (admin.sock) — ersetzt admin-net (Commit 2.4)
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
  - **`admin-net` ersatzlos entfernen** (Block unter `networks:` **und** aus
    `warden.networks`), ebenso die `warden`-Zeilen `ipv4_address: 172.31.0.2` und
    `ADMIN_HOST=172.31.0.2`. Stattdessen am `warden`-Service:
    ```yaml
    networks: [agent-net, egress-net]
    environment:
      - ADMIN_UDS=/run/warden/admin.sock          # Admin/Viewer über Unix-Socket (Commit 2.4)
    volumes:
      - ${PROJECT_DIR}/.catraz/run/warden:/run/warden   # Socket-Verzeichnis (Datei entsteht zur Laufzeit)
    healthcheck:
      test: ["CMD","python3","-c","import socket;socket.socket(socket.AF_UNIX).connect('/run/warden/admin.sock')"]
      interval: 10s
      timeout: 3s
      retries: 5
      start_period: 5s
    ```
    Damit gibt es **kein** festes Subnetz/keine feste IP mehr → parallele Sandboxes
    kollidieren nicht (jeder Socket liegt in seinem eigenen `.catraz/run/warden/`). Die
    Warden-Code-Seite folgt in Commit 2.4.
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
1. `<root>/.catraz/` + Unterordner (`config state/warden logs/warden logs/squid claude
   run/warden`) anlegen, `chown` `DEV_UID`.
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

## Commit 2.4 — Admin/Audit über Unix-Socket (löst die Parallel-Kollision)

Der Admin-/Viewer-Server (Port 9090) wandert von TCP+`admin-net` auf einen **Unix-Socket in
`.catraz/run/warden/`**. Kein Subnetz, keine IP, kein Port → parallele Sandboxes
kollisionsfrei (jeder Socket ist eine Datei im eigenen `.catraz`). Der Agent mountet das
Verzeichnis nie → keine Route dorthin (strikt sicherer als der bisherige Admin-TCP).

**Warden `warden/warden/__main__.py`** — Admin-Server auf UDS binden, wenn `ADMIN_UDS` gesetzt
ist (Proxy auf `:8080`/`agent-net` **unverändert**):
```python
import contextlib, os
...
    admin_uds = os.environ.get("ADMIN_UDS")
    if admin_uds:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(admin_uds)                      # stale socket von Crash entfernen
        admin_config = uvicorn.Config(create_admin_app(ctx), uds=admin_uds, log_level="warning")
    else:
        admin_config = uvicorn.Config(create_admin_app(ctx),
                                      host=cfg.admin_host, port=cfg.admin_port, log_level="warning")
    admin = uvicorn.Server(admin_config)
```
**`warden/docker-entrypoint.sh`** — Socket-Verzeichnis dem Warden-User geben:
```sh
chown -R warden:warden /var/lib/warden /var/log/warden /run/warden 2>/dev/null || true
```

**`src/catraz/cli.py`** — `AUDIT_URL`-Konstante entfernen; neuer Befehl `catraz audit`
(Imports oben in `cli.py` ergänzen: `contextlib, socket, socketserver, threading, webbrowser`):
```python

class _UdsProxy(socketserver.BaseRequestHandler):
    sock_path = ""           # per-instance via type(...)
    def handle(self):
        with socket.socket(socket.AF_UNIX) as up:
            up.connect(self.sock_path)
            def fwd(a, b):
                try:
                    while (d := a.recv(65536)): b.sendall(d)
                except OSError: pass
                finally:
                    with contextlib.suppress(OSError): b.shutdown(socket.SHUT_WR)
            t = threading.Thread(target=fwd, args=(self.request, up), daemon=True); t.start()
            fwd(up, self.request); t.join()

def cmd_audit(root, args, out):
    sock = root / ".catraz/run/warden/admin.sock"
    if not args.web:
        return _tail_audit(root, args, out)            # bestehender JSONL-Tail
    if not sock.exists():
        out.err("audit socket not found — run `catraz up` first"); return EXIT_GENERAL
    handler = type("H", (_UdsProxy,), {"sock_path": str(sock)})
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)   # ephemerer Port
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    out.info(f"audit viewer: {url}  (Ctrl-C to stop)"); webbrowser.open(url)
    try: srv.serve_forever()
    except KeyboardInterrupt: srv.shutdown()
    return EXIT_OK
```
Parser: `pa = sub.add_parser("audit", parents=[_g()]); pa.add_argument("--web", action="store_true");
pa.add_argument("-f","--follow",action="store_true"); pa.add_argument("--tail",type=int,default=100)`.
In `main` dispatchen. **`_print_urls`**: die alte `172.31.0.2:9090`-Zeile ersetzen durch
`"Audit viewer:  catraz audit --web   (host-only, ephemeral loopback port)"`.
**`_doctor_fix`**/Dir-Liste: `.catraz/run/warden` aufnehmen.

**Tests `tests/cli/test_audit.py`** (kein Docker — echten UDS lokal mocken):
```python
import socket, threading, urllib.request
from catraz import cli

def test_audit_web_forwards_to_uds(tmp_path):
    sockdir = tmp_path/".catraz/run/warden"; sockdir.mkdir(parents=True)
    sp = sockdir/"admin.sock"
    srv = socket.socket(socket.AF_UNIX); srv.bind(str(sp)); srv.listen()
    def serve():
        c,_ = srv.accept()
        c.recv(1024); c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"); c.close()
    threading.Thread(target=serve, daemon=True).start()
    import socketserver
    h = type("H",(cli._UdsProxy,),{"sock_path":str(sp)})
    fwd = socketserver.ThreadingTCPServer(("127.0.0.1",0), h)
    threading.Thread(target=fwd.serve_forever, daemon=True).start()
    body = urllib.request.urlopen(f"http://127.0.0.1:{fwd.server_address[1]}/").read()
    assert body == b"ok"; fwd.shutdown()
```

`commit: "feat(warden): serve admin over unix socket; catraz audit --web forwarder"`

## Akzeptanz Doc 02
- Unit-Tests grün.
- In leerem tmp-Repo: `catraz -C <dir> init` (mit `-y`/env) erzeugt vollständiges `.catraz/`
  inkl. `run/warden/`.
- `catraz -C <dir> up --print` zeigt `docker compose -f …/assets/compose/docker-compose.yml
  --project-directory <dir> --env-file <dir>/.catraz/.env up -d`.
- **Parallel-Kollision gelöst:** zwei `.catraz`-Projekte in verschiedenen Ordnern lassen sich
  gleichzeitig hochfahren; jedes hat seinen eigenen `admin.sock`; `catraz audit --web` öffnet
  je einen ephemeren Loopback-Port. Kein `admin-net`, keine feste IP mehr.
