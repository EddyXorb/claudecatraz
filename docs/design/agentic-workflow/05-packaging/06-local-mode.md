# 06 — Lokaler Modus: `catraz local` als drop-in `claude`

**Ziel:** `catraz local <claude-args>` fährt Claude im Sandbox-Container als one-off; Warden+
Squid bleiben als Daemons → schnell; `alias claude='catraz local'`. **Voraussetzung:** Doc 05
fertig. **Konventionen:** Tests `uv run --with pytest python -m pytest tests/ -q`; Commits
ohne Trailer.

## Commit 6.1 — Profil-Split: `up` infra-only, Agent-Daemon hinter `remote`

- **Asset-Compose**, Agent-Service `claude-dev-env`: `profiles: ["remote"]` hinzufügen. Damit
  startet ein profilloses `up` **nur** Warden+Squid; der Agent-Daemon kommt über das Profil.
  (`docker compose run claude-dev-env` aktiviert den Service trotz Profil automatisch.)
- **`cli.cmd_up`**: neues Flag `--remote`. Ohne `--remote` → `up -d` (nur Infra). Mit
  `--remote` → `--profile remote up -d` (Infra + Daemon). `compose.run` so aufrufen, dass das
  `--profile remote` *vor* `up` steht.
- **04-cli §5.3-Doku** und `_print_urls`: bei reinem `up` Hinweis „Agent-Daemon: `catraz up
  --remote`; interaktiv: `catraz local`".

**Tests `tests/cli/test_up_profile.py`** (`compose.run` mit `print_only`/Mock):
```python
from catraz import cli, compose
def test_up_without_remote_has_no_profile(monkeypatch, tmp_path):
    calls = {}
    monkeypatch.setattr(compose, "run", lambda root, args, **k: calls.setdefault("args", args))
    # … cmd_up mit args.remote=False aufrufen (Doctor/Infra-Teile mocken) …
    assert "--profile" not in calls["args"]
```
(Doctor-Preflight in `cmd_up` für den Test über `monkeypatch` auf `run_doctor`→leer.)

`commit: "feat(cli): up starts infra only; --remote adds the agent daemon profile"`

## Commit 6.2 — Entrypoint: lokaler Exec-Pfad

`assets/container/entrypoint.py`:
- `build_home(home, mode, remote=True)` — die RC-spezifischen Patches nur im Daemon-Pfad:
  ```python
  def build_home(home: Path, mode: str, remote: bool = True) -> None:
      ...
      if remote:
          data["bypassPermissionsModeAccepted"] = True
          data["remoteDialogSeen"] = True
      data.setdefault("projects", {}).setdefault("/workspace", {})["hasTrustDialogAccepted"] = True
      ...
  ```
- `cmd_local(claude_home, claude_args)`:
  ```python
  def cmd_local(claude_home: Path, claude_args: list[str]) -> None:
      drop_to_dev()
      mode = os.environ.get("AUTH_MODE", "subscription")
      if mode == "api_key" and not os.environ.get("ANTHROPIC_API_KEY"):
          sys.exit("error: api_key mode but ANTHROPIC_API_KEY unset")
      build_home(claude_home, mode, remote=False)
      configure_git_warden()
      os.execvp("claude", ["claude", *claude_args])
  ```
- `main`: `local`-Subparser mit `nargs=REMAINDER`; alles nach `local --` an `cmd_local`.
  ```python
  loc = sub.add_parser("local")
  loc.add_argument("rest", nargs=argparse.REMAINDER)   # ["--", "<args>"...]
  ...
  if args.command == "local":
      rest = args.rest[1:] if args.rest and args.rest[0] == "--" else args.rest
      cmd_local(Path(args.claude_home).resolve(), rest)
  ```

**Tests `tests/container/test_local.py`:**
```python
def test_build_home_local_no_bypass(ep, tmp_path, monkeypatch):
    home = tmp_path/".claude"; home.mkdir()
    monkeypatch.setattr(ep.Path, "home", staticmethod(lambda: tmp_path))
    ep.build_home(home, "api_key", remote=False)
    import json; cj = json.loads((tmp_path/".claude.json").read_text())
    assert "bypassPermissionsModeAccepted" not in cj
```

`commit: "feat(entrypoint): local exec path (claude <args>, normal permissions)"`

## Commit 6.3 — `catraz local` (CLI)

**`cli.py`** — Subparser **ohne** globale Flags (reiner Durchgriff):
```python
pl = sub.add_parser("local",
    help="run claude inside the sandbox (drop-in: alias claude='catraz local')")
pl.add_argument("claude_args", nargs=argparse.REMAINDER)
```
Globale catraz-Flags stehen *vor* `local` (`catraz -C <dir> local …`); alles nach `local`
gehört `claude` (inkl. `--dangerously-skip-permissions` für YOLO — kein eigenes `--yolo`).

**Reine, testbare Argument-Konstruktion:**
```python
import sys
from pathlib import Path

def _local_run_args(relpath: str, tty: bool, claude_args: list[str]) -> list[str]:
    args = ["run", "--rm", "--no-deps"]
    if not tty:
        args.append("-T")
    args += ["--workdir", f"/workspace/{relpath}".rstrip("/"),
             "claude-dev-env", "local", "--", *claude_args]
    return args

def cmd_local(root: Path, args, out) -> int:
    # find_root hat bereits fail-closed gegriffen (kein .catraz → CliError) -> nie Host-claude.
    compose.assert_real_dirs(root)
    auth.write_auth_fragment(root)
    compose.assert_invariants(root)                       # IMMER, ungecacht
    _ensure_infra(root, out)                              # lazy: Preflight+up nur kalt
    relpath = str(Path.cwd().resolve().relative_to(root))
    if relpath == ".":
        relpath = ""
    tty = sys.stdin.isatty()
    run_args = _local_run_args(relpath, tty, args.claude_args)
    r = compose.run(root, run_args, check=False)          # streamt TTY 1:1
    return r.returncode if r else EXIT_GENERAL
```
**`_ensure_infra(root, out)`**:
```python
def _ensure_infra(root, out):
    rows = compose.compose_ps(root)
    healthy = {r.get("Service") for r in rows if _row_ready(r)}
    if {"gitlab-warden", "forward-proxy"} <= healthy:
        return
    # kalt: Sicherheits-Preflight + Auth + (Auto-)Sync, dann Infra hochfahren
    f = run_doctor(root, only=SECURITY_SECTIONS)
    if print_findings(f, out)[0]:
        raise CliError("preflight failed — fix the ✘ above", EXIT_DOCTOR)
    _auto_sync_if_needed(root, out)                       # subscription: sync falls cred fehlt
    out.warn("catraz: sandbox active (warden+squid) — protects network/git, NOT your files")
    compose.run(root, ["up", "-d"], check=False)          # profillos = nur Infra
```
`main`: `catraz local` dispatcht zu `cmd_local(find_root(args.dir), args, out)`. `find_root`
wirft `CliError` ohne `.catraz` → **niemals** Host-`claude` (fail-closed).

**Tests `tests/cli/test_local.py`:**
```python
from catraz import cli

def test_run_args_passthrough_and_tty():
    a = cli._local_run_args("src/foo", tty=False, claude_args=["-p", "fix bug"])
    assert a[:4] == ["run", "--rm", "--no-deps", "-T"]
    assert "--workdir" in a and "/workspace/src/foo" in a
    # claude args appear verbatim after 'local --':
    assert a[a.index("local"):] == ["local", "--", "-p", "fix bug"]

def test_run_args_tty_omits_T_and_empty_workdir():
    a = cli._local_run_args("", tty=True, claude_args=[])
    assert "-T" not in a
    assert "/workspace" in a                       # rstrip("/") → "/workspace"
    assert a[a.index("local"):] == ["local", "--"]

def test_local_fails_closed_without_catraz(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                     # no .catraz here
    import pytest
    from catraz import paths, errors
    with pytest.raises(errors.CliError):
        paths.find_root()
```

`commit: "feat(cli): catraz local — sandboxed drop-in claude with always-on preflight"`

## Commit 6.4 — Doku & Alias

- `assets/.env.example`: Kommentar „interaktiv: `catraz local`; Daemon: `catraz up --remote`".
- `README.md`: Abschnitt „Lokaler Modus" mit `alias claude='catraz local'` und der
  Schützt/Schützt-nicht-Aussage (Netz/Git ja, Dateien nein).
- `cli-ci.yml`: Smoke `./catraz local --help` (zeigt den Subcommand-Help, kein Docker nötig,
  weil REMAINDER leer → argparse-Help).

`commit: "docs(cli): document local mode and the claude alias"`

## Akzeptanz Doc 06
- Unit-Tests grün.
- `catraz up` startet nur Warden+Squid; `docker ps` zeigt **keinen** Agent-Daemon.
- `catraz up --remote` startet zusätzlich den Daemon.
- Auf Docker-Host mit hochgefahrener Infra: `catraz local -p "echo hi"` läuft den Agenten
  one-off, Exit-Code durchgereicht; zweiter Aufruf ist schnell (Infra bleibt).
- Außerhalb eines `.catraz`-Projekts: `catraz local …` bricht mit Fehler ab (nie Host-`claude`).
- `compose.override.yml` mit `agent-net.internal: false` → `catraz local` bricht vor dem Start
  ab (Invariante).
