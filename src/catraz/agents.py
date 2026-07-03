"""§05.3 — the static Agent-Profile registry + manifest reader.

The host-side counterpart of the adapter contract (`agent_contract.py`,
shipped as a container asset). This module is real, installed package code
(not an asset) — it never loads adapter *logic*, only the declarative
`agent.toml` manifest, so `init`/`doctor`/tests can show a profile's command,
credential mode, and egress-domain suggestion without ever executing
adapter code on the host.

§06.2 anti-goal A2 ("kein dynamisches Plugin-Laden"): `AGENT_REGISTRY` is the
one static name -> asset-subdirectory mapping. `.catraz/.env`'s
`AGENT_PROFILE` selects a *name* from this fixed set; an unknown name is a
hard, fail-closed `CliError` — config never causes code to run that wasn't
already shipped.
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from catraz.envfile import load_env
from catraz.errors import CliError, EXIT_CONFIG
from catraz.paths import asset_root

# name -> asset subdirectory under assets/agents/. Adding a profile is a code
# change (a new `assets/agents/<name>/` + this one extra entry), never a
# config-driven addition.
AGENT_REGISTRY: dict[str, str] = {
    "claude": "claude",
}

DEFAULT_AGENT_PROFILE = "claude"


@dataclass(frozen=True)
class AgentManifest:
    """Host-side typed view of an `agent.toml` (§05.3)."""

    name: str
    command: str
    subscription_source: str
    api_key_env: str
    credentials_mode: str  # "sync" | "persistent"
    remote_allowed: bool
    debug_flag: str
    egress_domains: tuple[str, ...]


def resolve_agent_profile(root: Path) -> str:
    """The active profile name for this project: `.catraz/.env`'s
    `AGENT_PROFILE`, defaulting to `claude`. Fails closed on an unregistered
    name — a typo must never silently fall back to a different agent."""
    name = (load_env(root / ".catraz" / ".env").get("AGENT_PROFILE") or DEFAULT_AGENT_PROFILE).strip()
    if name not in AGENT_REGISTRY:
        raise CliError(
            f"unknown AGENT_PROFILE {name!r} — one of: {', '.join(sorted(AGENT_REGISTRY))}",
            EXIT_CONFIG,
        )
    return name


def agent_dir(profile: str) -> Path:
    """Asset directory for *profile* (`assets/agents/<profile>/`)."""
    return asset_root() / "assets" / "agents" / AGENT_REGISTRY[profile]


def _load_agent_contract() -> ModuleType:
    """Load `agent_contract.py` by path (it's a container asset, not an
    installed module — same by-path convention as `entrypoint.py`, see
    `tests/container/conftest.py`) and cache it in `sys.modules["agent_contract"]`.

    That cache entry matters beyond memoizing this one load: every adapter's
    `from agent_contract import ...` statement resolves via the module cache
    first, so once this has run, `load_adapter_module` can load an adapter
    straight from its real `assets/agents/<profile>/` location — no need to
    physically co-locate the files the way `layer.Dockerfile` does inside a
    built image (that flattening is a Docker-build concern, not a host one).
    """
    cached = sys.modules.get("agent_contract")
    if cached is not None:
        return cached
    contract = asset_root() / "assets" / "container" / "agent_contract.py"
    spec = importlib.util.spec_from_file_location("agent_contract", contract)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: agent_contract.py's dataclasses (under `from
    # __future__ import annotations`) resolve their string annotations via
    # `sys.modules[cls.__module__]` at class-definition time.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _read_toml(path: Path) -> dict[str, Any]:
    """Parse *path* via the shared `agent_contract.read_toml` (ODR). Typed
    `Any`-valued (TOML's own type, not re-declared here) — `load_manifest`
    is what gives the result a proper static shape."""
    result: dict[str, Any] = _load_agent_contract().read_toml(path)
    return result


def load_adapter_module(profile: str) -> ModuleType:
    """Load *profile*'s `adapter.py` by path, for host-side callers that need
    to run actual adapter logic (currently only `catraz sync`'s
    `sync_from_host`, §05.6 `credentials.mode = "sync"`). Never reads
    adapter code from `.catraz/` — only from the shipped `assets/agents/`
    tree, keyed by the static `AGENT_REGISTRY` (§06.2/A2)."""
    _load_agent_contract()  # ensures `from agent_contract import ...` resolves
    path = agent_dir(profile) / "adapter.py"
    if not path.exists():
        raise CliError(
            f"adapter not found: {path} (corrupt cache? remove ~/.cache/catraz)", EXIT_CONFIG
        )
    spec = importlib.util.spec_from_file_location("agent_adapter", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_manifest(profile: str) -> AgentManifest:
    """Parse `assets/agents/<profile>/agent.toml` into a typed manifest."""
    path = agent_dir(profile) / "agent.toml"
    if not path.exists():
        raise CliError(f"agent manifest not found: {path}", EXIT_CONFIG)
    data = _read_toml(path)
    creds: dict[str, Any] = data.get("credentials") or {}
    modes: dict[str, Any] = data.get("modes") or {}
    logs: dict[str, Any] = data.get("logs") or {}
    egress: dict[str, Any] = data.get("egress") or {}
    return AgentManifest(
        name=str(data["name"]),
        command=str(data["command"]),
        subscription_source=str(creds.get("subscription_source", "")),
        api_key_env=str(creds.get("api_key_env", "ANTHROPIC_API_KEY")),
        credentials_mode=str(creds.get("mode", "sync")),
        remote_allowed=bool(modes.get("remote", False)),
        debug_flag=str(logs.get("debug_flag", "--debug-file")),
        egress_domains=tuple(egress.get("domains") or ()),
    )
