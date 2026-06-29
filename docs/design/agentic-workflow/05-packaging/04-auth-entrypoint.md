# 04 — Auth-Modus (XOR) & Entrypoint-Umbau

**Ziel:** Genau **ein** Auth-Modus aktiv (`subscription` ⊻ `api_key`); Claude-Home als
RO-Einzeldateien + tmpfs; `.claude.json` immer provisioniert. **Voraussetzung:** Doc 03
fertig (Compose mit tmpfs-Home-Platz, `assert_invariants`). **Konventionen:** Tests `uv run
--with pytest python -m pytest tests/ -q`; Entrypoint-Funktionen per Pfad-Loader getestet;
Commits ohne Trailer.

`.catraz/.env` bekommt:
```
AUTH_MODE=subscription            # subscription | api_key
CLAUDE_CREDENTIAL_SOURCE=~/.claude
# ANTHROPIC_API_KEY=              # nur api_key
```
(`assets/.env.example` entsprechend ergänzen.)

## Commit 4.1 — `AUTH_MODE`, Auth-Compose-Fragment, `doctor auth`

**Base-Compose** (`assets/compose/docker-compose.yml`), Agent-Service:
- `ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}` aus `environment` **entfernen** (kommt nur noch
  im api_key-Fragment).
- `AUTH_MODE=${AUTH_MODE}` zu `environment` hinzufügen.
- Claude-Home-Volume aus Doc 03 (`bind … :/home/dev/.claude`) ersetzen durch:
  ```yaml
      - type: tmpfs
        target: /home/dev/.claude
  ```

**Neu `src/catraz/auth.py`:**
```python
from pathlib import Path
from catraz.envfile import load_env
from catraz.errors import CliError, EXIT_CONFIG

SUBSCRIPTION_FRAGMENT = """\
services:
  claude-dev-env:
    volumes:
      - { type: bind, source: ${PROJECT_DIR}/.catraz/claude/.credentials.json,
          target: /home/dev/.claude/.ro/.credentials.json, read_only: true }
      - { type: bind, source: ${PROJECT_DIR}/.catraz/claude/.claude.json,
          target: /home/dev/.claude/.ro/.claude.json, read_only: true }
"""
API_KEY_FRAGMENT = """\
services:
  claude-dev-env:
    environment:
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
"""

def auth_mode(root: Path) -> str:
    mode = load_env(root / ".catraz/.env").get("AUTH_MODE", "subscription")
    if mode not in ("subscription", "api_key"):
        raise CliError(f"AUTH_MODE must be subscription|api_key, got {mode!r}", EXIT_CONFIG)
    return mode

def write_auth_fragment(root: Path) -> Path:
    frag = root / ".catraz/.auth.compose.yml"
    frag.write_text(SUBSCRIPTION_FRAGMENT if auth_mode(root) == "subscription" else API_KEY_FRAGMENT)
    return frag
```

**`compose.base_cmd`**: das Auth-Fragment **immer** mit einbinden, *nach* der Base, *vor* dem
User-Override:
```python
    frag = root / ".catraz/.auth.compose.yml"
    if frag.exists():
        cmd += ["-f", str(frag)]
```
`cli.cmd_up` / später `cmd_local`: vor `compose.run(... up ...)` `auth.write_auth_fragment(root)`.

**`doctor.py`** — `check_auth(root, env, f)`:
```python
def check_auth(root, env, f):
    mode = env.get("AUTH_MODE", "")
    if mode not in ("subscription", "api_key"):
        f.bad("auth", "AUTH_MODE must be subscription|api_key", "set it in .catraz/.env"); return
    cred = paths.claude_home(root) / ".credentials.json"
    has_key = bool(env.get("ANTHROPIC_API_KEY"))
    if mode == "subscription":
        if has_key: f.bad("auth", "subscription mode but ANTHROPIC_API_KEY set", "unset it")
        if not cred.exists(): f.bad("auth", "no .credentials.json", "run `catraz sync`")
        else: f.ok("auth", "subscription credential present")
    else:
        if not has_key: f.bad("auth", "api_key mode but ANTHROPIC_API_KEY empty", "set it")
        if cred.exists(): f.bad("auth", "api_key mode but .credentials.json present (ambiguous)",
                                "remove .catraz/claude/.credentials.json")
        if has_key and not cred.exists(): f.ok("auth", "api_key set")
```
`"auth"` zu `DOCTOR_SECTIONS` **und** `SECURITY_SECTIONS` hinzufügen; in `run_doctor`
dispatchen.

**Tests `tests/cli/test_auth.py`:**
```python
from catraz import auth, doctor
from catraz.errors import CliError
import pytest

def _mk(tmp_path, env):
    (tmp_path/".catraz").mkdir(); (tmp_path/".catraz/.env").write_text(env)
    (tmp_path/".catraz/claude").mkdir()
    return tmp_path

def test_mode_invalid(tmp_path):
    _mk(tmp_path, "AUTH_MODE=both\n")
    with pytest.raises(CliError): auth.auth_mode(tmp_path)

def test_fragment_subscription(tmp_path):
    _mk(tmp_path, "AUTH_MODE=subscription\n"); auth.write_auth_fragment(tmp_path)
    assert ".credentials.json" in (tmp_path/".catraz/.auth.compose.yml").read_text()

def test_fragment_api_key(tmp_path):
    _mk(tmp_path, "AUTH_MODE=api_key\n"); auth.write_auth_fragment(tmp_path)
    assert "ANTHROPIC_API_KEY" in (tmp_path/".catraz/.auth.compose.yml").read_text()

def test_doctor_auth_xor(tmp_path):
    root = _mk(tmp_path, "")
    f = doctor.Findings()
    doctor.check_auth(root, {"AUTH_MODE":"api_key","ANTHROPIC_API_KEY":"x"}, f)
    (root/".catraz/claude/.credentials.json").write_text("{}")
    f2 = doctor.Findings(); doctor.check_auth(root, {"AUTH_MODE":"api_key","ANTHROPIC_API_KEY":"x"}, f2)
    assert any(i[0]==doctor.BAD for i in f2.items)   # cred present in api_key → bad
```

`commit: "feat(auth): AUTH_MODE XOR, auth compose fragment, doctor auth check"`

## Commit 4.2 — Entrypoint-Umbau (RO-Home, copy-then-patch)

`src/catraz/assets/container/entrypoint.py` umbauen. **Ersetzen** von
`ensure_claude_json`/`ensure_settings`/`ensure_agent_memory` durch **eine** `build_home`;
`cmd_start` modus-abhängig:
```python
def build_home(home: Path, mode: str) -> None:
    """Build the tmpfs Claude-home each start. RO sources live under home/.ro/."""
    home.mkdir(parents=True, exist_ok=True)
    ro = home / ".ro"
    if mode == "subscription":
        src = ro / ".credentials.json"
        if not src.exists():
            sys.exit("error: subscription mode but no .credentials.json mounted (run `catraz sync`)")
        shutil.copy2(src, home / ".credentials.json")
    # .claude.json lives at the HOME ROOT (sibling of ~/.claude), NOT inside the tmpfs dir.
    if mode == "subscription" and (ro / ".claude.json").exists():
        data = read_json(ro / ".claude.json")
    else:
        data = {"hasCompletedOnboarding": True, "lastOnboardingVersion": "1.0"}
    data["bypassPermissionsModeAccepted"] = True
    data["remoteDialogSeen"] = True
    data.setdefault("projects", {}).setdefault("/workspace", {})["hasTrustDialogAccepted"] = True
    (Path.home() / ".claude.json").write_text(json.dumps(data, indent=2))
    (home / "settings.json").write_text(
        json.dumps({"theme": "dark", "hasCompletedOnboarding": True}, indent=2))
    agent_md = Path("/opt/claude-dev-env/AGENT.md")
    if agent_md.exists():
        shutil.copy2(agent_md, home / "CLAUDE.md")


def cmd_start(claude_home: Path) -> None:
    drop_to_dev()
    mode = os.environ.get("AUTH_MODE", "subscription")
    if mode == "api_key" and not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("error: api_key mode but ANTHROPIC_API_KEY unset")
    build_home(claude_home, mode)
    configure_git_warden()
    os.execvp("claude", ["claude", "remote-control", "--permission-mode", "bypassPermissions",
                         "--spawn", "same-dir", "--debug-file", str(claude_home / "rc-debug.log")])
```
- Den Symlink-Trick (`target.symlink_to(stored)`) **ersatzlos entfernen**.
- `configure_git_warden`, `drop_to_dev`, `read_json` unverändert lassen.
- `main`/`--claude-home`-Default bleibt; `cmd_sync` → Commit 4.3.

**Tests `tests/container/test_build_home.py`** (Pfad-Loader wie in Doc 01):
```python
def test_build_home_subscription(ep, tmp_path, monkeypatch):
    home = tmp_path / ".claude"; (home/".ro").mkdir(parents=True)
    (home/".ro"/".credentials.json").write_text("{}")
    (home/".ro"/".claude.json").write_text('{"organizationUuid":"org"}')
    monkeypatch.setattr(ep.Path, "home", staticmethod(lambda: tmp_path))
    ep.build_home(home, "subscription")
    assert (home/".credentials.json").exists()
    import json; cj = json.loads((tmp_path/".claude.json").read_text())
    assert cj["organizationUuid"] == "org" and cj["bypassPermissionsModeAccepted"] is True
    assert (home/"settings.json").exists()

def test_build_home_api_key_synthesizes(ep, tmp_path, monkeypatch):
    home = tmp_path / ".claude"; home.mkdir()
    monkeypatch.setattr(ep.Path, "home", staticmethod(lambda: tmp_path))
    ep.build_home(home, "api_key")
    assert not (home/".credentials.json").exists()
    assert (tmp_path/".claude.json").exists()
```
(`ep`-Fixture = per Pfad geladenes Entrypoint-Modul; in `tests/container/conftest.py`.)

`commit: "refactor(entrypoint): RO-home copy-then-patch, mode-aware start"`

## Commit 4.3 — `.claude.json`-Provisionierung, `sync`-Quelle, Auto-Sync

**Entrypoint `cmd_sync`** (Quelle konfigurierbar; `.claude.json` immer vorhanden danach):
```python
def cmd_sync(claude_home: Path, source: str | None = None) -> None:
    src_dir = Path(source or os.environ.get("CLAUDE_CREDENTIAL_SOURCE")
                   or str(Path.home() / ".claude")).expanduser()
    cred = src_dir / ".credentials.json"
    if not cred.exists():
        sys.exit(f"error: {cred} not found — authenticate with `claude` on the host first")
    claude_home.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cred, claude_home / ".credentials.json")
    host_cj = src_dir.parent / ".claude.json"          # ~/.claude.json sits next to ~/.claude/
    dst_cj = claude_home / ".claude.json"
    if host_cj.exists():
        shutil.copy2(host_cj, dst_cj)
    elif not dst_cj.exists():
        dst_cj.write_text(json.dumps(
            {"hasCompletedOnboarding": True, "lastOnboardingVersion": "1.0"}, indent=2))
    print(f"Credentials synced into {claude_home}")
```
`entrypoint.main`: `sync`-Subparser bekommt `--from`/`source`-Arg, das an `cmd_sync` geht.

**`cli._run_sync`** reicht `CLAUDE_CREDENTIAL_SOURCE` (aus `.catraz/.env`) bzw. `--from` an den
Entrypoint weiter (Env `CLAUDE_CREDENTIAL_SOURCE` setzen ODER `--from` als Arg).

**`cli.cmd_init`** (api_key wie subscription): nach dem Anlegen von `.catraz/claude/` **immer**
ein `.claude.json` sicherstellen (Default schreiben, falls weder Host-Kopie noch vorhanden) —
damit der subscription-RO-Bind nie ins Leere zeigt. Praktisch: im Subscription-Fall ruft
`init` ohnehin `sync` (das materialisiert beides); im api_key-Fall direkt den Default
schreiben.

**`cli.cmd_up`** Auto-Sync: ist `AUTH_MODE=subscription` und `.catraz/claude/.credentials.json`
fehlt, einmal `_run_sync` versuchen; bleibt sie fehlend → `EXIT_DOCTOR` mit klarer Meldung.

**Tests `tests/container/test_sync.py`:**
```python
def test_sync_materializes_claude_json(ep, tmp_path):
    src = tmp_path/"src"/".claude"; src.mkdir(parents=True)
    (src/".credentials.json").write_text("{}")                 # no host ~/.claude.json
    home = tmp_path/"dst"
    ep.cmd_sync(home, source=str(src))
    assert (home/".credentials.json").exists()
    assert (home/".claude.json").exists()                      # synthesized default
```

`commit: "feat(auth): always provision .claude.json; configurable sync source; auto-sync"`

## Akzeptanz Doc 04
- Unit-Tests grün.
- `doctor` meldet ❌ bei beiden-oder-keinem Auth-Pfad.
- Auf Docker-Host: subscription-`up` startet; im Container `cat ~/.claude.json` zeigt die
  gepatchten Felder; `echo x > ~/.claude/.ro/.credentials.json` scheitert (RO) → Red-Team **T5**
  ergänzen; nach Neustart überleben keine vom Agenten geschriebenen `settings.json`-Hooks → **T6**.
