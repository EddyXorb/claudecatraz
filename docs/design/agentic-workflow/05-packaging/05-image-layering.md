# 05 — Image-Schichtung: Base ⊕ Claude-Layer

**Ziel:** Beliebige Toolchain-Base + catraz-eigener Claude-Layer (`FROM ${BASE_IMAGE}`).
**Voraussetzung:** Doc 04 fertig (Auth, Entrypoint-Umbau; monolithisches
`assets/Dockerfile` baut den Agenten). **Base-Vertrag:** Debian/Ubuntu (apt) + glibc +
`python3` + `curl`. **Konventionen:** Tests `uv run --with pytest python -m pytest tests/ -q`;
Commits ohne Trailer.

## Commit 5.1 — `assets/Dockerfile` in Base + Claude-Layer splitten

- **`assets/bases/cpp-rust-python/Dockerfile`** = der Toolchain-Teil des heutigen
  `assets/Dockerfile`: `FROM ubuntu:24.04`, apt-Build-Tools, `uv`+`conan`, LLVM, Rust.
  **Ohne** Node, Claude-Code, dev-User, gosu, `COPY entrypoint/AGENT.md`, ENTRYPOINT.
- **`assets/claude-layer/Dockerfile`** = der catraz-Teil, **oben** auf der Base:
  ```dockerfile
  ARG BASE_IMAGE
  FROM ${BASE_IMAGE}
  ARG NODE_VERSION=22
  ARG CLAUDE_CODE_VERSION=latest
  ARG DEV_UID=1000
  ENV DEBIAN_FRONTEND=noninteractive
  RUN apt-get update && apt-get install -y gosu && \
      curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash - && \
      apt-get install -y nodejs && rm -rf /var/lib/apt/lists/* && \
      npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}
  # UID-1000-Konflikt (Ubuntus `ubuntu`-User) auflösen, sonst scheitert useradd:
  RUN (userdel -r ubuntu 2>/dev/null || true) && useradd -m -u ${DEV_UID} -s /bin/bash dev
  COPY container/entrypoint.py /entrypoint.py
  COPY AGENT.md /opt/claude-dev-env/AGENT.md
  ENV HOME=/home/dev
  WORKDIR /workspace
  ENTRYPOINT ["python3", "/entrypoint.py"]
  ```
  > **Kein `USER dev`**: der Container startet als root, der Entrypoint chownt `/workspace`
  > und droppt via `gosu` auf `dev` (bestehendes, bewährtes Muster; Claude Code verweigert
  > root → Drop ist erzwungen). Der Build-Kontext ist `assets/` (enthält `container/` + `AGENT.md`).
- `assets/Dockerfile` löschen.
- **Asset-Compose**, Agent-`build`:
  ```yaml
    build:
      context: ..
      dockerfile: claude-layer/Dockerfile
      args:
        BASE_IMAGE: ${BASE_IMAGE}
        NODE_VERSION: ${NODE_VERSION:-22}
        CLAUDE_CODE_VERSION: ${CLAUDE_CODE_VERSION:-latest}
        DEV_UID: ${DEV_UID:-1000}
  ```
- `assets/.dockerignore`: `bases/` ist schon ausgeschlossen; sicherstellen, dass
  `claude-layer/` **nicht** ausgeschlossen ist (es ist das Dockerfile-Verzeichnis, Kontext
  bleibt `..`).

> **Korrektur B3:** Sobald der Claude-Layer ein `ENTRYPOINT` hat, **muss** die `command:`-Zeile
> aus dem Compose-Service `claude-dev-env` entfernt werden. Bleibt sie stehen, hängt Docker
> das `command` als Argumente an den ENTRYPOINT — `python3 /entrypoint.py python3
> /entrypoint.py` — und argparse bricht ab. In der Umsetzung wurde `command:` entfernt;
> diese Korrektur gehört in den Commit, der den `claude-layer/Dockerfile` mit `ENTRYPOINT`
> einführt.

**Tests `tests/cli/test_image_assets.py`:**
```python
from catraz.paths import asset_root
def test_layer_dockerfiles_present():
    ar = asset_root() / "assets"
    cl = (ar / "claude-layer/Dockerfile").read_text()
    assert "ARG BASE_IMAGE" in cl and "FROM ${BASE_IMAGE}" in cl
    assert (ar / "bases/cpp-rust-python/Dockerfile").exists()
```

`commit: "refactor(image): split base toolchain from FROM-base claude layer"`

## Commit 5.2 — `image.py`: Base auflösen/bauen, Tag-Hash, `prune`

`.catraz/.env` (+ `assets/.env.example`):
```
# leer → mitgelieferte Default-Base; sonst eines von:
# BASE_IMAGE=ghcr.io/acme/devenv:1.4
# BASE_DOCKERFILE=./Dockerfile.dev
```

**Neu `src/catraz/image.py`:**
```python
import hashlib, subprocess
from pathlib import Path
from catraz.paths import asset_root
from catraz.envfile import load_env
from catraz.errors import CliError, EXIT_DOCKER

def _image_exists(tag: str) -> bool:
    return subprocess.run(["docker", "image", "inspect", tag],
                          capture_output=True).returncode == 0

def _build_base(dockerfile: Path) -> str:
    tag = f"catraz-base:{hashlib.sha256(dockerfile.read_bytes()).hexdigest()[:12]}"
    if not _image_exists(tag):
        r = subprocess.run(["docker", "build", "-t", tag,
                            "-f", str(dockerfile), str(dockerfile.parent)])
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
        return _build_base(df)
    return _build_base(asset_root() / "assets/bases/cpp-rust-python/Dockerfile")

def prune() -> None:
    r = subprocess.run(["docker", "image", "ls", "catraz-base", "--format", "{{.Repository}}:{{.Tag}}"],
                       capture_output=True, text=True)
    for tag in r.stdout.split():
        subprocess.run(["docker", "image", "rm", tag], capture_output=True)
```

**`compose.run`**: optionalen `extra_env: dict | None`-Parameter ergänzen, der in das
Subprozess-`env` gemergt wird.

**`cli.cmd_up`**: nur wenn gebaut wird (`--build`) zuerst `base = image.resolve_base(root)`,
dann `compose.run(root, ["up","-d","--build"], extra_env={"BASE_IMAGE": base})`. Ohne
`--build` kein Base-Resolve.

**Neuer Befehl `catraz prune`** → `image.prune()`.

**Tests `tests/cli/test_image.py`:**
```python
from pathlib import Path
from catraz import image

def test_tag_is_content_addressed(tmp_path, monkeypatch):
    df = tmp_path / "Dockerfile"; df.write_text("FROM ubuntu:24.04\n")
    seen = {}
    monkeypatch.setattr(image, "_image_exists", lambda t: False)

    def fake_run(cmd, **k):
        seen.setdefault("tag", cmd[cmd.index("-t") + 1])
        return type("R", (), {"returncode": 0})()
    monkeypatch.setattr(image.subprocess, "run", fake_run)
    image._build_base(df)
    assert seen["tag"].startswith("catraz-base:") and len(seen["tag"].split(":")[1]) == 12

def test_resolve_prefers_base_image(tmp_path):
    (tmp_path/".catraz").mkdir(); (tmp_path/".catraz/.env").write_text("BASE_IMAGE=x/y:1\n")
    assert image.resolve_base(tmp_path) == "x/y:1"
```

> **Korrektur B5:** Das ursprüngliche Lambda `seen.setdefault("tag", …) or type("R",…)()` ist
> nicht lauffähig: `dict.setdefault` gibt den gespeicherten Wert zurück (truthy String) →
> `or` schließt kurz und das Lambda liefert den String statt des Fake-Result-Objekts →
> `AttributeError: 'str' object has no attribute 'returncode'`. Die obige Vorlage ersetzt das
> Lambda durch eine benannte `fake_run`-Funktion mit identischem Capture-Ziel und Assertion.

`commit: "feat(image): resolve/build base (BASE_IMAGE|BASE_DOCKERFILE|default), prune"`

## Commit 5.3 — `doctor base`

**`doctor.py`** — `check_base(root, env, f)`:
```python
def check_base(root, env, f):
    if not which("docker"):
        f.warn("base", "docker missing — base not checked"); return
    try:
        base = image.resolve_base(root)
    except CliError as e:
        f.bad("base", str(e)); return
    contract = subprocess.run(
        ["docker", "run", "--rm", base, "sh", "-c", "command -v apt-get && python3 --version"],
        capture_output=True, text=True)
    if contract.returncode != 0:
        f.bad("base", "base lacks apt-get or python3", "base contract: Debian/Ubuntu + python3")
    else:
        f.ok("base", f"base contract ok ({base})")
    setuid = subprocess.run(["docker", "run", "--rm", base, "find", "/", "-perm", "/6000",
                             "-type", "f"], capture_output=True, text=True)
    extra = [ln for ln in setuid.stdout.split() if ln]
    if extra:
        f.warn("base", f"{len(extra)} setuid/setgid binaries in base", "review: " + ", ".join(extra[:5]))
```
`"base"` zu `DOCTOR_SECTIONS` hinzufügen (nicht zwingend in `SECURITY_SECTIONS` — Base-Build
ist teuer; nur `doctor`/`up --build` triggert es). In `run_doctor` dispatchen.

**Tests `tests/cli/test_doctor_base.py`** (Docker gemockt):
```python
from catraz import doctor, image
def test_base_contract_fail(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "which", lambda c: True)
    monkeypatch.setattr(image, "resolve_base", lambda r: "base:tag")
    class R: 
        def __init__(s, rc, out=""): s.returncode, s.stdout = rc, out
    monkeypatch.setattr(doctor.subprocess, "run",
        lambda cmd, **k: R(1) if "apt-get" in " ".join(cmd) else R(0, ""))
    f = doctor.Findings(); doctor.check_base(tmp_path, {}, f)
    assert any(i[0]==doctor.BAD for i in f.items)
```

`commit: "feat(doctor): base contract + setuid scan via doctor base"`

## Akzeptanz Doc 05
- Unit-Tests grün.
- Default-Base: `catraz up --build` baut `catraz-base:<hash>` dann den Claude-Layer
  `FROM` davon; Agent läuft wie zuvor (Toolchain vorhanden).
- `BASE_IMAGE=ubuntu:24.04` in `.env` + `up --build`: Claude-Layer baut auf `ubuntu:24.04`,
  `doctor base` ok.
- `catraz prune` entfernt `catraz-base:*`.
