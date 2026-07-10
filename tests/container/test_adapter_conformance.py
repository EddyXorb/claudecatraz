"""Adapter conformance harness (no Docker required): runs the security
contract against every adapter in `catraz.agents.AGENT_REGISTRY`, checking
no Forge/foreign-model credential leaks via `environ()`/`prepare_home()`,
`remote_command()` denies fail-closed when `modes.remote = false`, and
`render_instructions()` never names the Warden container directly."""

import importlib.util
import shutil
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

from catraz.agents import AGENT_REGISTRY, load_manifest

_REPO = Path(__file__).resolve().parents[2]
_CONTAINER = _REPO / "src/catraz/assets/container"
_AGENTS_ROOT = _REPO / "src/catraz/assets/agents"

FORBIDDEN_CREDENTIAL_MARKERS = (
    "GITLAB_READ_TOKEN",
    "GITLAB_WRITE_TOKEN",
    "GITLAB_API_TOKEN",
)


def _stage(profile: str, dst: Path) -> Path:
    """Flatten entrypoint assets + one profile's adapter into `dst` (the same
    layout `layer.Dockerfile` produces), return the staged adapter.py path."""
    shutil.copy2(_CONTAINER / "agent_contract.py", dst / "agent_contract.py")
    shutil.copy2(_CONTAINER / "git_routing.py", dst / "git_routing.py")
    src_dir = _AGENTS_ROOT / AGENT_REGISTRY[profile]
    shutil.copy2(src_dir / "adapter.py", dst / "agent_adapter.py")
    shutil.copy2(src_dir / "agent.toml", dst / "agent.toml")
    tmpl = src_dir / "AGENT.md.tmpl"
    if tmpl.exists():
        shutil.copy2(tmpl, dst / "AGENT.md.tmpl")
    return dst / "agent_adapter.py"


def _load(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("agent_adapter", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # see agent_contract.py's Secrets/InstructionContext
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(params=sorted(AGENT_REGISTRY))
def profile(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def adapter(profile: str, tmp_path_factory: pytest.TempPathFactory) -> Any:
    staged = tmp_path_factory.mktemp(f"conformance-{profile}")
    return _load(_stage(profile, staged))


def _secrets_for(adapter: Any, **overrides: Any) -> Any:
    base = dict(
        auth_mode="api_key",
        subscription_ro_dir=None,
        api_key_file=None,
        api_key_env_fallback="dummy-key",
        remote=False,
    )
    base.update(overrides)
    return adapter.Secrets(**base)


def test_contract_surface_present(adapter: Any) -> None:
    for fn in (
        "prepare_home",
        "command",
        "environ",
        "render_instructions",
        "remote_command",
    ):
        assert hasattr(adapter, fn), f"adapter missing contract function: {fn}"


def test_environ_carries_no_forge_credential(adapter: Any) -> None:
    env = adapter.environ(_secrets_for(adapter))
    for key, value in env.items():
        for marker in FORBIDDEN_CREDENTIAL_MARKERS:
            assert marker not in key.upper(), f"environ() leaked a Forge credential key: {key}"
            assert marker not in str(value).upper(), (
                f"environ() leaked a Forge credential value via {key}"
            )


def test_environ_only_uses_manifests_own_api_key_env(profile: str, adapter: Any) -> None:
    manifest = load_manifest(profile)
    env = adapter.environ(_secrets_for(adapter))
    for key in env:
        assert key == manifest.api_key_env, (
            f"environ() set {key!r}, which is not this profile's declared "
            f"credentials.api_key_env ({manifest.api_key_env!r}) — looks like a "
            "foreign-model credential"
        )


def test_prepare_home_writes_no_forge_credential(
    adapter: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "agent-home"
    home.mkdir()
    monkeypatch.setattr(adapter.Path, "home", staticmethod(lambda: tmp_path))
    secrets = _secrets_for(adapter)
    adapter.prepare_home(home, secrets)
    for p in home.rglob("*"):
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for marker in FORBIDDEN_CREDENTIAL_MARKERS:
            assert marker not in text.upper(), f"{p} written by prepare_home() contains {marker}"


def test_remote_false_denies_fail_closed(
    profile: str, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """`modes.remote = false` must make `remote_command()` return None, never
    a half-built daemon command."""
    staged = tmp_path_factory.mktemp(f"conformance-remote-off-{profile}")
    adapter_path = _stage(profile, staged)
    manifest_path = staged / "agent.toml"
    data = tomllib.loads(manifest_path.read_text())
    data.setdefault("modes", {})["remote"] = False
    _write_toml(manifest_path, data)
    mod = _load(adapter_path)
    assert mod.remote_command() is None


def test_render_instructions_points_at_warden_not_direct_forge(
    adapter: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rendered REST base is a generic per-host rule, never a direct
    Forge URL and never the Warden's own container name."""
    monkeypatch.setattr(adapter.Path, "home", staticmethod(lambda: tmp_path))
    ctx = adapter.InstructionContext(
        forge_rest_base="http://<host>:8080/api/v4",
        branch_prefixes=("claude/",),
        warden_toml_path=Path("/etc/catraz/warden.toml"),
    )
    _, content = adapter.render_instructions(ctx)
    assert "http://<host>:8080/api/v4" in content
    assert "gitlab-warden" not in content  # never leak the Warden's own container name
    assert "https://gitlab.com/api/v4" not in content  # no direct-Forge REST base


def _write_toml(path: Path, data: dict[str, Any]) -> None:
    """Minimal TOML serializer for the flat 2-level shape `agent.toml` uses —
    no external dependency needed for this one test helper."""
    lines = []
    top = {k: v for k, v in data.items() if not isinstance(v, dict)}
    for k, v in top.items():
        lines.append(f"{k} = {_toml_value(v)}")
    for section, values in data.items():
        if not isinstance(values, dict):
            continue
        lines.append(f"[{section}]")
        for k, v in values.items():
            lines.append(f"{k} = {_toml_value(v)}")
    path.write_text("\n".join(lines) + "\n")


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return '"' + str(v).replace('"', '\\"') + '"'
