"""The static Agent-Profile registry + manifest reader.

Host-side counterpart of the adapter contract; never loads adapter logic,
only the declarative agent.toml manifest. AGENT_REGISTRY is a fixed
name -> asset-subdirectory mapping; an unknown profile is a fail-closed CliError.
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
# change, never a config-driven addition.
AGENT_REGISTRY: dict[str, str] = {
    "claude": "claude",
}

# Profiles shipped by this codebase, not derived from AGENT_REGISTRY — a fork
# that registers an additional profile there stays out of this set.
SHIPPED_PROFILES: frozenset[str] = frozenset({"claude"})

DEFAULT_AGENT_PROFILE = "claude"

# Valid values of `.catraz/.env`'s CLAUDE_CREDENTIALS_MODE override, mirroring
# AgentManifest.credentials_mode's own two values.
CREDENTIALS_MODES = ("persistent", "sync")


@dataclass(frozen=True)
class AgentManifest:
    """Host-side typed view of an agent.toml manifest."""

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
    name = (
        load_env(root / ".catraz" / ".env").get("AGENT_PROFILE") or DEFAULT_AGENT_PROFILE
    ).strip()
    if name not in AGENT_REGISTRY:
        raise CliError(
            f"unknown AGENT_PROFILE {name!r} — one of: {', '.join(sorted(AGENT_REGISTRY))}",
            EXIT_CONFIG,
        )
    return name


def effective_credentials_mode(root: Path, env: dict[str, str] | None = None) -> str:
    """The mode that governs this project's credential storage: `.catraz/.env`'s
    `CLAUDE_CREDENTIALS_MODE` when it is `persistent`/`sync`, otherwise the
    active profile's manifest default. *env* lets a caller that already loaded
    `.catraz/.env` reuse it instead of reading the file again."""
    e = env if env is not None else load_env(root / ".catraz" / ".env")
    override = e.get("CLAUDE_CREDENTIALS_MODE", "").strip()
    if override in CREDENTIALS_MODES:
        return override
    return load_manifest(resolve_agent_profile(root)).credentials_mode


def agent_dir(profile: str) -> Path:
    """Asset directory for *profile* (`assets/agents/<profile>/`)."""
    return asset_root() / "assets" / "agents" / AGENT_REGISTRY[profile]


def _load_agent_contract() -> ModuleType:
    """Load agent_contract.py by path (a container asset, not an installed module)
    and cache it in sys.modules so every adapter's `from agent_contract import ...`
    resolves without needing the files physically co-located."""
    cached = sys.modules.get("agent_contract")
    if cached is not None:
        return cached
    contract = asset_root() / "assets" / "container" / "agent_contract.py"
    spec = importlib.util.spec_from_file_location("agent_contract", contract)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: agent_contract.py's dataclasses resolve their string
    # annotations via sys.modules[cls.__module__] at class-definition time.
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
    """Load profile's adapter.py by path, for host-side callers that need to run
    actual adapter logic (currently only `catraz sync`). Never reads adapter code
    from `.catraz/` — only from the shipped assets/agents/ tree."""
    _load_agent_contract()  # ensures `from agent_contract import ...` resolves
    path = agent_dir(profile) / "adapter.py"
    if not path.exists():
        raise CliError(
            f"adapter not found: {path} (corrupt cache? remove ~/.cache/catraz)",
            EXIT_CONFIG,
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
