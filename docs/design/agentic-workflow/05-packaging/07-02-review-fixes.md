# 07 — Review-Fixes (B1–B9) & CI-Reparatur

**Ziel:** Die während der Umsetzung von Doc 04–06 in `TODO.md` festgehaltenen Review-Befunde
**B1–B9** abarbeiten und die drei roten CI-Workflows (dockerfile-lint, squid, redteam) wieder
grün machen. Damit werden die noch offenen TODO-Designziele geschlossen (insb. „von überall
lauffähig" via B2, „beliebige Dockerfiles" via B6).

**Voraussetzung:** Stand `feat/packaging` nach externer Umsetzung von Doc 01–06 (Paket unter
`src/catraz/`, `.catraz/`-Heim, Asset-Layering, `catraz local`/`up --remote`). **Konventionen
(aus `00-overview.md`):** Python ≥ 3.11, stdlib-only für `catraz`; Tests `uv run --with pytest
python -m pytest tests/ -q` müssen vor jedem Commit grün sein (Redteam-Tests skippen ohne
Docker-Daemon — das ist OK); Commits ohne Trailer, Conventional-Commits-Betreff, ein Commit pro
„Commit N.x"-Block, Autor = Repo-Eigner; Arbeit auf `feat/packaging`, nicht auf `main`.

**Designentscheidungen (vom Nutzer bestätigt):**
- **B7** → *nur dokumentieren* (striktes RO beibehalten; Limitierung sichtbar machen).
- **B6** → *`BASE_CONTEXT`-env* (minimaler, gezielter Fix; keine volle BYO-Validierung).
- **redteam-CI** → *Fixture härten* (Dummy-Secrets, init-Exit tolerieren, Docker-Runner; bleibt
  gatender Job).

## Überblick: was ist schon erledigt vs. offen

| Befund | Status im Code | In diesem Plan |
| ------ | -------------- | -------------- |
| B1 .env.example tote Defaults | **offen** (Zeilen vorhanden) | Commit 7.1 |
| B2 `_run_sync` Pfad ins Projekt statt Asset | **offen** (echter Bug) | Commit 7.2 |
| B3 `command:`/`ENTRYPOINT`-Doppelaufruf | bereits korrekt im Code (`command:` entfernt) | nur Doc-Notiz 7.8 |
| B4 Asset-Cache invalidiert nie bei Dev | **offen** (Footgun) | Commit 7.3 |
| B5 nicht lauffähiger Doc-Test | im Code umgangen | nur Doc-Notiz 7.8 |
| B6 `BASE_CONTEXT` fehlt | **offen** | Commit 7.4 |
| B7 Token-Refresh nicht persistent | **offen** (bewusst RO) | Commit 7.5 (Doku) |
| B8 doppelter Test-Basename / importlib-Mode | im Code gelöst (`--import-mode=importlib`) | nur Doc-Notiz 7.8 |
| B9 `.gitattributes` ohne LF-Pin | **offen** | Commit 7.6 |
| CI dockerfile-lint Pfad | **rot** (Root-`Dockerfile` weg) | Commit 7.7 |
| CI squid Pfad | **rot** (`config/` verschoben) | Commit 7.8 |
| CI redteam Fixture | **rot** (`init` Exit 3; `up` infra-only) | Commit 7.9 |

---

## Commit 7.1 — B1: tote `.env.example`-Defaults streichen

`src/catraz/assets/.env.example` endet mit den nach der `.catraz`-Migration toten Zeilen:
```ini
CLAUDE_HOME=./claude
PROJECT_DIR=./workspace
```
`PROJECT_DIR` wird von `compose.run` zur Laufzeit gesetzt (Prozess-Env schlägt `--env-file`),
`CLAUDE_HOME` ist seit Doc 04 (tmpfs-Home + `CLAUDE_CREDENTIAL_SOURCE`) bedeutungslos.

- Beide Zeilen samt zugehörigem Kommentar-Block („Mount paths …") entfernen.
- `AUTH_MODE` (Zeile 27) und `CLAUDE_CREDENTIAL_SOURCE` (Zeile 28) stehen bereits im Example —
  **nur prüfen, nicht erneut hinzufügen.**

**Test `tests/cli/test_env_example.py`** — die **ausgelieferte** Kopie über `asset_root()` lesen
(funktioniert in Wheel **und** Zero-Install; `_repo_root()` ist im Wheel `None` → `TypeError`):
```python
from catraz import paths
def test_env_example_has_no_dead_mount_vars(tmp_path, monkeypatch):
    monkeypatch.setattr(paths.Path, "home", lambda: tmp_path)
    txt = (paths.asset_root() / "assets" / ".env.example").read_text()
    assert "PROJECT_DIR=" not in txt
    assert "CLAUDE_HOME=" not in txt
    assert "AUTH_MODE=" in txt
```

`commit: "build(env): drop dead CLAUDE_HOME/PROJECT_DIR from .env.example"`

## Commit 7.2 — B2: Sync-Entrypoint aus dem Asset-Cache auflösen

`cli._run_sync` löst die Host-seitige Sync-Tool-Datei relativ zum **gesandboxten Projekt** auf:
```python
entry = root / "src" / "catraz" / "assets" / "container" / "entrypoint.py"
```
In einem beliebigen Projektordner existiert `<root>/src/catraz/...` nicht → `catraz sync`,
der Sync-Schritt in `init` und der Auto-Sync in `up` brechen mit „entrypoint.py not found".
Korrekt ist der Pfad, über den alle anderen Assets schon aufgelöst werden:

```python
# lokaler Import in _run_sync (wie die übrigen paths-Importe in cli.py, z. B. Zeile 89/236):
from catraz.paths import asset_root
entry = asset_root() / "assets" / "container" / "entrypoint.py"
if not entry.exists():
    raise CliError("entrypoint.py asset not found (corrupt cache? remove ~/.cache/catraz)", EXIT_GENERAL)
```
- **Import-Stil festlegen:** `_run_sync` importiert `asset_root` **lokal** (nicht modul-global) —
  konsistent mit den bestehenden lokalen `from catraz.paths import claude_home`-Importen in
  `cli.py`. Dadurch ist im Test **`paths.asset_root`** das wirksame Patch-Ziel (kein Doppel-Patch).
- `cwd=root` im `subprocess.run` belassen (unschädlich; `--claude-home` ist bereits absolut).

**Test `tests/cli/test_sync_entry.py`** (kein Docker — `subprocess.run` mocken, Pfad prüfen):
```python
import types
import pytest
from catraz import cli, paths
from catraz.errors import CliError

def _seed(tmp_path):
    (tmp_path / ".catraz").mkdir()
    (tmp_path / ".catraz/.env").write_text("AUTH_MODE=subscription\n")

def test_run_sync_uses_asset_entrypoint(tmp_path, monkeypatch):
    _seed(tmp_path)
    fake_assets = tmp_path / "cache"
    entry = fake_assets / "assets/container/entrypoint.py"
    entry.parent.mkdir(parents=True); entry.write_text("# tool")
    monkeypatch.setattr(paths, "asset_root", lambda: fake_assets)   # local import → this is the live symbol
    seen = {}
    monkeypatch.setattr(cli.subprocess, "run",
                        lambda cmd, **k: seen.update(cmd=cmd) or types.SimpleNamespace(returncode=0))
    cli._run_sync(tmp_path, cli.Out(color=False))
    assert str(entry) in seen["cmd"]

def test_run_sync_raises_when_asset_missing(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setattr(paths, "asset_root", lambda: tmp_path / "empty")
    with pytest.raises(CliError):
        cli._run_sync(tmp_path, cli.Out(color=False))
```

`commit: "fix(cli): resolve sync entrypoint from asset cache, not project root"`

## Commit 7.3 — B4: Asset-Cache im Zero-Install-Modus invalidieren

`paths.asset_root()` extrahiert nach `~/.cache/catraz/<__version__>/` und kehrt bei
vorhandenem `.extracted`-Marker **immer** zurück. Im Zero-Install-/Dev-Betrieb (Quelle = Repo,
Version bleibt `0.2.0`) propagiert dadurch keine Asset-Änderung, bis man den Cache von Hand
löscht. Für veröffentlichte Wheels ist der versionsbasierte Marker korrekt; nur der
**Quellbaum-Zweig** (`_repo_root()` gefunden) braucht eine Frische-Prüfung.

**Designkorrektur nach Roast (zwei Blocker vermieden):**
1. Der Frische-Scan **darf nicht** `__pycache__`/`*.pyc`/`.venv`/`.git` einbeziehen — sonst löst
   jeder Testlauf (der `entrypoint.cpython-*.pyc` neben dem Asset erzeugt) und jedes `uv sync`
   in `warden/.venv` eine Dauer-Re-Extraktion aus. Junk wird sowohl beim **Scan** als auch beim
   **Copy** ausgeschlossen.
2. Die Frische-Entscheidung hängt **nicht** am mtime des Markers selbst (Race + Test-Bruch),
   sondern: der Marker **speichert die zum Extraktionszeitpunkt gesehene Quell-Signatur**
   (max. Quell-mtime, junk-bereinigt). Bei jedem Aufruf wird die Signatur neu berechnet und mit
   dem Marker-Inhalt verglichen.

Umbau in `asset_root()`:
```python
_IGNORE = shutil.ignore_patterns(".venv", "__pycache__", "*.pyc", ".git", "*.egg-info")

def _source_signature(*roots: Path) -> str:
    newest = 0.0
    for r in roots:
        if not r.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(r):
            dirnames[:] = [d for d in dirnames if d not in (".venv", "__pycache__", ".git")]
            for name in filenames:
                if name.endswith(".pyc"):
                    continue
                try:
                    newest = max(newest, (Path(dirpath) / name).stat().st_mtime)
                except OSError:
                    pass
    return repr(newest)

def asset_root() -> Path:
    dst = Path.home() / ".cache" / "catraz" / __version__
    marker = dst / ".extracted"
    repo = _repo_root()
    sig = _source_signature(repo / "src/catraz/assets", repo / "warden",
                            repo / "forward-proxy") if repo else ""
    if marker.exists():
        if repo is None:                       # installed wheel: version-keyed, trust marker
            return dst
        if marker.read_text() == sig:          # zero-install: source unchanged
            return dst
        shutil.rmtree(dst / "assets", ignore_errors=True)   # stale dev cache → rebuild clean
    (dst / "assets").mkdir(parents=True, exist_ok=True)
    pkg_assets = ir.files("catraz") / "assets"
    if pkg_assets.is_dir() and (pkg_assets / "warden").is_dir():   # installed wheel
        with ir.as_file(pkg_assets) as src:
            shutil.copytree(src, dst / "assets", dirs_exist_ok=True)
    else:                                       # zero-install source tree
        assert repo, "cannot locate assets"
        shutil.copytree(repo / "src/catraz/assets", dst / "assets",
                        dirs_exist_ok=True, ignore=_IGNORE)
        for ctx in ("warden", "forward-proxy"):
            shutil.copytree(repo / ctx, dst / "assets" / ctx,
                            dirs_exist_ok=True, ignore=_IGNORE)
    marker.write_text(sig)                      # store the signature we just extracted
    return dst
```
- Die `if marker.exists(): return dst`-Frühausstieg-Logik wird **ersetzt** (nicht nur ergänzt):
  der Extraktionsblock läuft jetzt unbedingt unterhalb der Guard. Der `ignore=_IGNORE` beim
  Copy hält die zuvor mitkopierte `warden/.venv` aus dem Cache heraus (Nebeneffekt: kleinerer,
  schnellerer Cache).
- `_source_signature` nutzt `os.walk` mit `dirnames`-Pruning (kein `rglob`, das `.venv` doch
  betreten würde).

**Test `tests/cli/test_paths.py`** ergänzen — Quell-mtime im `finally` zurücksetzen, Re-Extraktion
über den **Marker-Inhalt** (Signatur) nachweisen, nicht über dessen mtime:
```python
import os
def test_asset_cache_refreshes_on_source_change(tmp_path, monkeypatch):
    monkeypatch.setattr(paths.Path, "home", lambda: tmp_path)
    root = paths.asset_root()
    sig1 = (root / ".extracted").read_text()
    src = paths._repo_root() / "src/catraz/assets/compose/docker-compose.yml"
    orig = src.stat().st_mtime
    try:
        os.utime(src, (orig + 1000, orig + 1000))     # source "changed" → newer mtime
        paths.asset_root()                              # second resolution must re-extract
        sig2 = (root / ".extracted").read_text()
        assert sig2 != sig1                             # signature changed → cache rebuilt
    finally:
        os.utime(src, (orig, orig))                     # leave the working tree mtimes intact

def test_asset_cache_stable_without_change(tmp_path, monkeypatch):
    monkeypatch.setattr(paths.Path, "home", lambda: tmp_path)
    sig1 = (paths.asset_root() / ".extracted").read_text()
    sig2 = (paths.asset_root() / ".extracted").read_text()
    assert sig1 == sig2                                 # no churn when source is unchanged
```
> Der zweite Test ist die Regression gegen Blocker 1 (Junk-Churn): liefe der Scan über `.pyc`,
> würde ein dazwischen erzeugtes `entrypoint.cpython-*.pyc` die Signatur ändern und den Test
> röten.

`commit: "fix(paths): refresh asset cache when source assets change (zero-install)"`

## Commit 7.4 — B6: `BASE_CONTEXT` entkoppelt Build-Kontext vom Dockerfile-Verzeichnis

`image._build_base` baut mit `docker build -f <dockerfile> <dockerfile.parent>` — der Kontext
ist fix das Dockerfile-Verzeichnis, was kontextrelative `COPY`/`ADD` in benutzereigenen Bases
bricht (TODO-Ziel „beliebige Dockerfiles").

`src/catraz/image.py` erweitern:
```python
def _build_base(dockerfile: Path, context: Path | None = None) -> str:
    ctx = context or dockerfile.parent
    tag = f"catraz-base:{hashlib.sha256(dockerfile.read_bytes()).hexdigest()[:12]}"
    if not _image_exists(tag):
        r = subprocess.run(["docker", "build", "-t", tag,
                            "-f", str(dockerfile), str(ctx)])
        if r.returncode:
            raise CliError(f"base build failed (Dockerfile {dockerfile})", EXIT_DOCKER)
    return tag

def resolve_base(root: Path) -> str:
    env = load_env(root / ".catraz/.env")
    if env.get("BASE_IMAGE"):
        return env["BASE_IMAGE"]
    if env.get("BASE_DOCKERFILE"):
        df = (root / env["BASE_DOCKERFILE"]).resolve()
        if not df.exists():
            raise CliError(f"BASE_DOCKERFILE not found: {df}", EXIT_DOCKER)
        ctx = None
        if env.get("BASE_CONTEXT"):
            ctx = (root / env["BASE_CONTEXT"]).resolve()
            if not ctx.is_dir():
                raise CliError(f"BASE_CONTEXT not a directory: {ctx}", EXIT_DOCKER)
        return _build_base(df, ctx)
    return _build_base(asset_root() / "assets/bases/cpp-rust-python/Dockerfile")
```
- `BASE_CONTEXT` ist **relativ zum Projekt-Root** (wie `BASE_DOCKERFILE`), Default = Verzeichnis
  des Dockerfiles (= bisheriges Verhalten, abwärtskompatibel).
- `assets/.env.example` um einen kommentierten Block ergänzen:
  ```ini
  # Eigene Base statt der mitgelieferten cpp/rust/python-Toolchain:
  # BASE_IMAGE=ghcr.io/you/base:tag           # fertiges Image (kein Build)
  # BASE_DOCKERFILE=./docker/Dockerfile.base  # eigenes Dockerfile (relativ zum Projekt)
  # BASE_CONTEXT=.                             # Build-Kontext (Default: Dockerfile-Verzeichnis)
  ```

**Test `tests/cli/test_image.py`** ergänzen (subprocess mocken, Kontext-Arg prüfen):
```python
import types
from catraz import image

def _seed(tmp_path, env):
    (tmp_path/".catraz").mkdir(); (tmp_path/".catraz/.env").write_text(env)

def test_base_context_overrides_build_dir(tmp_path, monkeypatch):
    df = tmp_path/"docker"/"Dockerfile.base"; df.parent.mkdir(parents=True); df.write_text("FROM scratch\n")
    (tmp_path/"ctxroot").mkdir()
    _seed(tmp_path, "BASE_DOCKERFILE=./docker/Dockerfile.base\nBASE_CONTEXT=./ctxroot\n")
    seen = {}
    def fake_run(cmd, **k):
        seen["cmd"] = cmd; return types.SimpleNamespace(returncode=0)
    monkeypatch.setattr(image.subprocess, "run", fake_run)
    monkeypatch.setattr(image, "_image_exists", lambda t: False)
    image.resolve_base(tmp_path)
    assert str((tmp_path/"ctxroot").resolve()) in seen["cmd"]
    assert str(df.resolve()) in seen["cmd"]

def test_base_context_default_is_dockerfile_dir(tmp_path, monkeypatch):
    df = tmp_path/"docker"/"Dockerfile.base"; df.parent.mkdir(parents=True); df.write_text("FROM scratch\n")
    _seed(tmp_path, "BASE_DOCKERFILE=./docker/Dockerfile.base\n")
    seen = {}
    monkeypatch.setattr(image.subprocess, "run", lambda cmd, **k: seen.update(cmd=cmd) or types.SimpleNamespace(returncode=0))
    monkeypatch.setattr(image, "_image_exists", lambda t: False)
    image.resolve_base(tmp_path)
    assert str(df.parent.resolve()) in seen["cmd"]
```

`commit: "feat(image): BASE_CONTEXT to decouple build context from Dockerfile dir"`

## Commit 7.5 — B7: Token-Refresh-Limitierung dokumentieren

Designentscheidung: striktes RO bleibt; die Persistenz-Lücke wird **sichtbar** gemacht statt
behoben. Im Subscription-Modus liegt die Host-`.credentials.json` RO unter `.ro/` und wird in
ein tmpfs kopiert — in-Session refreshte/rotierte Tokens überleben keinen Container-Stop.

- **`README.md`** (Abschnitt Auth/Subscription): kurzen Hinweis ergänzen — refreshte Tokens
  persistieren nicht; bei Auth-Bruch nach längerer Pause hilft erneutes `catraz sync`. Begründung
  (RO schützt die Host-Credential vor dem Agenten) nennen.
- **`assets/.env.example`** bei `CLAUDE_CREDENTIAL_SOURCE` einen Kommentar ergänzen, der auf die
  Persistenz-Grenze hinweist.
- **`doctor.check_auth`**: im Subscription-Zweig (wenn Credential vorhanden) ein **`warn`**
  ergänzen: „subscription token refreshes are not persisted across restarts — re-run `catraz
  sync` if auth breaks". Reiner Hinweis, kein Gate (kein `bad`).

**Test `tests/cli/test_auth.py`** ergänzen:
```python
def test_doctor_auth_warns_about_refresh_persistence(tmp_path):
    from catraz import doctor
    (tmp_path/".catraz/claude").mkdir(parents=True)
    (tmp_path/".catraz/claude/.credentials.json").write_text("{}")
    f = doctor.Findings()
    doctor.check_auth(tmp_path, {"AUTH_MODE": "subscription"}, f)
    # tie the assertion to the auth SECTION so a misplaced warn elsewhere can't pass it
    assert any(i[0] == doctor.WARN and i[1] == "auth" and "persist" in i[2].lower()
               for i in f.items)
```

`commit: "docs(auth): document subscription token-refresh non-persistence (B7)"`

## Commit 7.6 — B9: `.gitattributes` auf LF pinnen

`.gitattributes` pinnt nur `*.sh` und `Dockerfile*`. Der extensionslose Root-Shim `catraz`
(`#!/usr/bin/env python3`) ist nicht abgedeckt → unter `core.autocrlf=true` wird er beim Checkout
zu CRLF und `./catraz` bricht (`/usr/bin/env: »python3\r«`). Auch `*.py`/`*.yml`/`*.toml` tragen
denselben latenten Konflikt.

`.gitattributes` ergänzen:
```gitattributes
# Der extensionslose Root-Shim ist ein Python-Skript — CRLF bricht den Shebang.
catraz   text eol=lf
*.py     text eol=lf
*.yml    text eol=lf
*.yaml   text eol=lf
*.toml   text eol=lf
```
- Nach dem Commit lokal `git add --renormalize .` ausführen, damit bereits eingecheckte Blobs
  konsistent als LF normalisiert werden (i. d. R. No-Op, da die committeten Blobs schon LF sind).
  Das ist ein **manueller** Schritt; auf dem Linux-CI-Runner (`autocrlf` aus) ohnehin No-Op.

**Test:** keiner nötig (reines Repo-Hygiene-Artefakt, keine Code-Logik). Akzeptanz: nach
frischem Checkout startet `./catraz --version`. (Manuell / via CI-Smoke abgedeckt.)

`commit: "build(git): pin catraz shim and text assets to LF (B9)"`

## Commit 7.7 — CI: dockerfile-lint auf verschobene Dockerfiles richten

Der Root-`Dockerfile` existiert nicht mehr; das Agent-Image ist in
`assets/bases/cpp-rust-python/Dockerfile` (Base) + `assets/claude-layer/Dockerfile` (Claude-Layer)
gesplittet. `dockerfile-lint.yml` lintet noch `Dockerfile` (Root) → `does not exist`.

`.github/workflows/dockerfile-lint.yml`:
- `paths:`-Trigger (push + pull_request) auf die realen Pfade umstellen:
  `src/catraz/assets/bases/cpp-rust-python/Dockerfile`,
  `src/catraz/assets/claude-layer/Dockerfile`, `forward-proxy/Dockerfile`, `warden/Dockerfile`,
  und der Workflow-Datei selbst.
- `matrix.dockerfile` auf dieselben vier Pfade setzen (statt `Dockerfile`).

`commit: "ci(dockerfile): lint relocated asset Dockerfiles"`

## Commit 7.8 — CI: squid auf `assets/config` richten (+ Doc-Notizen B3/B5/B8)

`config/` wurde nach `src/catraz/assets/config/` verschoben; `squid-ci.yml` referenziert noch
`config/squid.conf`/`config/allowlist.txt` → Config-Lint und Smoke-Test brechen.

`.github/workflows/squid-ci.yml`:
- `paths:`-Trigger (push + pull_request): `config/squid.conf`/`config/allowlist.txt` →
  `src/catraz/assets/config/squid.conf`/`…/allowlist.txt`.
- Job `lint`: `open("config/allowlist.txt")` und die `grep`-Pfade auf
  `src/catraz/assets/config/...` umstellen.
- Job `smoke`: die beiden `-v "$PWD/config/...:..."`-Mounts auf
  `$PWD/src/catraz/assets/config/...` umstellen.
- Der `./forward-proxy`-Build-Kontext bleibt unverändert (Verzeichnis liegt weiter im Repo-Root).

**Doc-Korrekturen (B3/B5/B8 — reine Plan-Doc-Pflege, kein Code):** In den jeweiligen Doc-Dateien
einen kurzen „Korrektur"-Hinweis ergänzen, damit die Vorlagen 1:1 lauffähig sind:
- **B3** `05-packaging/05-image-layering.md`: notieren, dass mit `ENTRYPOINT` die
  `command:`-Zeile aus dem Compose entfernt werden **muss** (sonst Doppelinvokation).
- **B5** dito: das nicht lauffähige `setdefault`-Lambda durch die benannte `fake_run`-Variante
  ersetzen (Tag merken, Result-Objekt zurückgeben).
- **B8** `05-packaging/06-local-mode.md`: notieren, dass die zwei gleichnamigen `test_local.py`
  den globalen `--import-mode=importlib` erfordern (in `pyproject.toml` gesetzt).

`commit: "ci(squid): point at relocated assets/config; note doc fixes B3/B5/B8"`

## Commit 7.9 — CI: redteam grün (gatende Docker-Primitive + gehärtete Fixture für `slow`)

**Befund (Roast-bestätigt):** Der Voll-Stack-Lauf in der Standard-CI ist **nicht** verlässlich
machbar — drei harte Gründe:
1. `claude-dev-env` hängt an `profiles: ["remote"]` (Compose Zeile 71); `catraz up` ohne
   `--remote` startet den Agenten gar nicht → `exec claude-dev-env` läuft ins Leere (eigentliche
   Ursache der 3 CI-Errors, nicht nur der `init`-Exit-3).
2. `entrypoint.cmd_start` führt `claude remote-control` aus; mit einem **Dummy**-
   `ANTHROPIC_API_KEY` scheitert die Anthropic-Auth → Container endet sofort, `exec` schlägt fehl.
   Ein echter Key gehört nicht in einen öffentlichen CI-Runner.
3. Der Agent-Image-Build (Base cpp/rust/python: LLVM/Rust/Conan + Claude-Layer) dauert auf
   `ubuntu-latest` ohne Cache ~20–45 min → langsam und flaky.

**Konsequenz / Designentscheidung (Nutzerwahl „Fixture härten + gatender Job" — feasibel
umgesetzt):** Die redteam-CI **gatet die Docker-Primitive, die die Vertrauensgrenze direkt
beweisen und keinen catraz-Stack brauchen** (`docker run alpine`): **T2** (tmpfs-Ordering),
**T7a** (Container-Symlink bleibt im Namespace), **T8** (`mountinfo` ohne Host-Secret-Pfad). Die
**live-stack**-Tests **T1/T3/T4** bleiben `@pytest.mark.slow` und werden **nicht** im
Standard-Runner gegatet — die zugehörige Fixture wird dennoch **gehärtet**, damit sie lokal /
auf einem Runner mit echtem Key + Docker manuell grün läuft.

**Marker-Neuklassifizierung** (`tests/redteam/test_shadow_mount.py`): T7a und T8 nutzen nur
`docker run alpine` (kein catraz-Stack) — den irreführenden `@pytest.mark.slow` von **T7a und
T8 entfernen**; nur **T1/T3/T4** (live-stack-Fixture) bleiben `@slow`. Danach gatet
`-m "not slow"` genau die drei stack-freien Primitive.

**`.github/workflows/redteam-ci.yml`:** den pytest-Aufruf auf
`uv run --with pytest python -m pytest tests/redteam/ -m "not slow" -q` setzen (Runner hat Docker;
`_docker_available()` greift, T2/T7a/T8 laufen real). Optional einen Kommentar ergänzen, dass die
`slow`-Tests einen Stack + echten `ANTHROPIC_API_KEY` brauchen und lokal/manuell laufen.

**Fixture-Härtung** (für die lokalen `slow`-Läufe — `live_stack` in derselben Datei):
- `init` mit `check=False` aufrufen (Exit-3 des Abschluss-`doctor` tolerieren; das `.catraz`-
  Scaffold ist trotzdem angelegt).
- Danach ein vollständiges `.catraz/.env` schreiben (Reihenfolge: **nach** `init`, damit es nicht
  überschrieben wird):
  ```python
  (root/".catraz/.env").write_text(
      "AUTH_MODE=api_key\n"
      "ANTHROPIC_API_KEY=" + os.environ.get("ANTHROPIC_API_KEY", "sk-ci-dummy") + "\n"
      "GITLAB_READ_TOKEN=ci-dummy\n"
      "GITLAB_WRITE_TOKEN=ci-dummy\n"
      "WARDEN_ALLOWED_PROJECTS=acme/demo\n"
      f"DEV_UID={os.getuid()}\n"
  )
  ```
  `AUTH_MODE=api_key` umgeht den Subscription-Credential-Zwang; `tokens` ist **nicht** in
  `SECURITY_SECTIONS`, gated `up` also nicht.
- `catraz up` → **`catraz up --remote`** (Agent-Daemon starten).
- Teardown `down` behalten.
- **DEV_UID-Hinweis:** Das Image backt `dev` per **Build-Arg** `DEV_UID` (Default 1000); ein
  Runner-`os.getuid()` ≠ 1000 kann zu Mount-Ownership-Abweichungen führen. Für die `slow`-Tests
  (laufen lokal, meist uid 1000) unkritisch; falls relevant, `--build`/Build-Arg angleichen.
  Da T1/T3/T4 nicht CI-gatend sind, wird das hier nur dokumentiert, nicht gelöst.

**Test/Marker-Konsistenz:** `pyproject.toml` definiert `slow` bereits als Marker (aus Doc 06) —
keine Änderung nötig.

**Akzeptanz 7.9:** `redteam CI` grün; der Job führt real T2/T7a/T8 unter Docker aus (nicht
nur „0 selected"). Begründung der Variante im `progress.md` festhalten.

---

## Akzeptanz Doc 07
- `uv run --with pytest python -m pytest tests/ -q` grün (Unit + Container; Redteam skippt ohne
  Docker-Daemon lokal).
- `./catraz --version` startet auch nach frischem Checkout (B9).
- `catraz sync` / `init`-Sync funktioniert aus einem **fremden** Ordner (installiert), nicht nur
  im Repo-Klon (B2) — manuell mit `uv tool install . && cd /tmp/proj && catraz init` verifizieren.
- Asset-Änderung im Quellbaum propagiert ohne manuelles Cache-Löschen (B4).
- `BASE_DOCKERFILE`+`BASE_CONTEXT` baut mit dem angegebenen Kontext (B6).
- Alle drei zuvor roten CI-Workflows (dockerfile-lint, squid, redteam) grün; die schon grünen
  (cli, compose, warden) bleiben grün.
