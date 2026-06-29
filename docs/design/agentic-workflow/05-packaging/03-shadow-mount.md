# 03 — Shadow-Mount: `.catraz` für den Agenten unlesbar

**Ziel:** Der Agent sieht `/workspace` (das Projekt) **ohne** `/workspace/.catraz` (tmpfs
überdeckt es); Manipulation der Vertrauensgrenze wird vor jedem Start geprüft.
**Voraussetzung:** Doc 02 fertig (`.catraz/`, Asset-Compose, `compose.run`, `find_root`).
**Konventionen:** Tests `uv run --with pytest python -m pytest tests/ -q`; Docker-Tests in
`tests/redteam/` (eigenes Job, skip ohne Docker); Commits ohne Trailer.

## Commit 3.1 — Mindest-Docker-Version + T2-Spike

- **`doctor.py` `check_docker`** erweitern: Compose-Version parsen und Mindestversion
  erzwingen — **Docker Engine ≥ 24, Compose ≥ 2.20**. Darunter `f.bad("docker", …)` →
  `up`/`local` verweigern.
- **`tests/redteam/test_shadow_mount.py`** mit T2 als erstem, gating Test (skip wenn
  `shutil.which("docker")` fehlt):
  ```python
  import shutil, subprocess, textwrap, pytest
  pytestmark = pytest.mark.skipif(not shutil.which("docker"), reason="needs docker")

  def _run(cmd): return subprocess.run(cmd, capture_output=True, text=True)

  def test_t2_tmpfs_overdeck_ordering(tmp_path):
      """tmpfs over a bind subpath masks host content deterministically."""
      (tmp_path / ".catraz").mkdir(); (tmp_path / ".catraz/secret").write_text("TOP")
      r = _run(["docker","run","--rm",
                "-v", f"{tmp_path}:/workspace",
                "--tmpfs","/workspace/.catraz",
                "alpine","sh","-c","ls -A /workspace/.catraz | wc -l"])
      assert r.returncode == 0
      assert r.stdout.strip() == "0"          # .catraz appears EMPTY to the container
  ```
- Schlägt T2 fehl, ist Doc 03 blockiert (Fallback Pre-Start-Mount — separat zu entscheiden).

`commit: "feat(doctor): enforce min docker/compose; add T2 tmpfs-ordering spike"`

## Commit 3.2 — Compose: Langform-Bind + tmpfs-Shadow

Asset-`docker-compose.yml`, Agent-Service: die **Kurzform**-Volumes aus Doc 02 durch
**Langform** ersetzen (deterministische Mount-Ordnung):
```yaml
    volumes:
      - type: bind
        source: ${PROJECT_DIR}
        target: /workspace
      - type: tmpfs
        target: /workspace/.catraz
        tmpfs: { size: 1048576, mode: 0700 }
      - type: bind                       # Claude-Home (RO-Topologie folgt in Doc 04)
        source: ${PROJECT_DIR}/.catraz/claude
        target: /home/dev/.claude
```
`cli.cmd_up`: vor dem Compose-Aufruf sicherstellen, dass `<root>/.catraz` existiert
(`paths` legt es in `init` an; defensiv `mkdir` falls fehlend).

**Tests `tests/redteam/test_shadow_mount.py`** ergänzen (docker-gated): T1/T3/T4 gegen einen
hochgefahrenen Stack — als Helfer eine Fixture, die in einem tmp-Projekt `catraz init -y` +
`catraz up` ausführt und am Ende `down`. T1 `ls -A /workspace/.catraz`→leer; T3 Schreiben ins
tmpfs verändert Host-`.catraz` nicht; T4 `umount /workspace/.catraz` als `dev`→`EPERM`.
(Diese Tests dürfen `@pytest.mark.slow` sein.)

`commit: "feat(compose): shadow-mount /workspace/.catraz with empty tmpfs"`

## Commit 3.3 — Quellpfad-Symlink-Guard + aufgelöste-Compose-Invarianten

**`src/catraz/compose.py`** ergänzen:
```python
import json
from catraz.errors import CliError, EXIT_CONFIG

def assert_real_dirs(root) -> None:
    for p in (root, root / ".catraz"):
        if p.is_symlink():
            raise CliError(f"{p} is a symlink — bind source must be a real dir", EXIT_CONFIG)

def _env_keys(agent) -> set[str]:
    env = agent.get("environment") or {}
    if isinstance(env, list):
        return {e.split("=", 1)[0] for e in env}
    return set(env.keys())

def assert_invariants(root) -> None:
    r = run(root, ["config", "--format", "json"], capture=True, check=False)
    if r is None or r.returncode != 0:
        raise CliError("docker compose config failed (cannot verify trust boundary)", EXIT_CONFIG)
    cfg = json.loads(r.stdout)
    if not cfg.get("networks", {}).get("agent-net", {}).get("internal"):
        raise CliError("invariant: agent-net is not internal", EXIT_CONFIG)
    agent = cfg["services"]["claude-dev-env"]
    if any(k.startswith("GITLAB_") and k.endswith("_TOKEN") for k in _env_keys(agent)):
        raise CliError("invariant: agent carries a GITLAB_*_TOKEN", EXIT_CONFIG)
    if agent.get("privileged") or "SYS_ADMIN" in (agent.get("cap_add") or []):
        raise CliError("invariant: agent is privileged / CAP_SYS_ADMIN", EXIT_CONFIG)
    vols = agent.get("volumes", [])
    if not any(v.get("type") == "tmpfs" and v.get("target") == "/workspace/.catraz" for v in vols):
        raise CliError("invariant: tmpfs shadow on /workspace/.catraz missing", EXIT_CONFIG)
```
**`cli.cmd_up`**: vor `compose.run(... up ...)` aufrufen: `compose.assert_real_dirs(root)`
**und** `compose.assert_invariants(root)`. Schlägt eine fehl → Exit `EXIT_DOCTOR` (3) mit der
Meldung.

**Tests `tests/cli/test_invariants.py`** (kein Docker — JSON-Parser direkt testen, indem
`run` gemonkeypatcht wird):
```python
import json, types
from catraz import compose
from catraz.errors import CliError
import pytest

GOOD = {"networks":{"agent-net":{"internal":True}},
        "services":{"claude-dev-env":{"environment":{},"volumes":[
            {"type":"tmpfs","target":"/workspace/.catraz"}]}}}

def _patch(monkeypatch, cfg):
    monkeypatch.setattr(compose, "run",
        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=json.dumps(cfg)))

def test_invariants_pass(monkeypatch, tmp_path):
    _patch(monkeypatch, GOOD); compose.assert_invariants(tmp_path)   # no raise

@pytest.mark.parametrize("mut", [
    lambda c: c["networks"]["agent-net"].__setitem__("internal", False),
    lambda c: c["services"]["claude-dev-env"]["environment"].__setitem__("GITLAB_WRITE_TOKEN","x"),
    lambda c: c["services"]["claude-dev-env"].__setitem__("privileged", True),
    lambda c: c["services"]["claude-dev-env"].__setitem__("volumes", []),
])
def test_invariants_fail(monkeypatch, tmp_path, mut):
    import copy; bad = copy.deepcopy(GOOD); mut(bad); _patch(monkeypatch, bad)
    with pytest.raises(CliError): compose.assert_invariants(tmp_path)

def test_symlink_guard(tmp_path):
    (tmp_path/".catraz").mkdir(); link = tmp_path/"l"; link.symlink_to(tmp_path)
    with pytest.raises(CliError): compose.assert_real_dirs(link)
```

`commit: "feat(compose): symlink guard + resolved-compose trust-boundary invariants"`

## Commit 3.4 — Red-Team T7/T8 + CI-Job

- `tests/redteam/test_shadow_mount.py`: T7a (Symlink im Container löst im Container-Namespace
  auf — kein Host-Pfad), T8 (`/proc/self/mountinfo` zeigt keinen *erreichbaren* Secret-Pfad),
  T7b (host-seitiger Symlink auf `${PROJECT_DIR}` → `catraz up` bricht ab — nutzt
  `assert_real_dirs`, kein Docker, kann in `tests/cli/` liegen).
- **Neuer Workflow `.github/workflows/redteam-ci.yml`** (Docker-runner): `uv run --with pytest
  python -m pytest tests/redteam/ -q` auf `ubuntu-latest` (hat Docker).

`commit: "test(redteam): shadow-mount negative tests T1–T8 + CI job"`

## Akzeptanz Doc 03
- Unit-Tests (`tests/cli/`) grün ohne Docker.
- Auf Docker-Host: `pytest tests/redteam/test_shadow_mount.py` grün (T1–T4, T7, T8).
- `catraz up` bricht ab, wenn man testweise in `compose.override.yml` `agent-net.internal:
  false` setzt (Invariante greift).
- **Noch offen für Doc 04:** T5 (Credential RO) und T6 (settings-Overwrite) brauchen die
  RO-Home-Topologie — dort ergänzen.
