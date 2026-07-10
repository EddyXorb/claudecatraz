"""The Agent-Adapter contract shared by the generic entrypoint and every
`assets/agents/<name>/adapter.py`. Loaded by path, never by package import.
Each `layer.Dockerfile` copies it next to `entrypoint.py` and the chosen
`adapter.py` so a plain `import agent_contract` resolves without importlib."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


def read_toml(path: Path) -> dict[str, Any]:
    """Parse a manifest (`agent.toml`) — the one shared TOML reader: both
    the in-container adapter and the host-side `catraz.agents` module call
    this instead of duplicating `tomllib` plumbing."""
    return tomllib.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Secrets:
    """File-based secret material the entrypoint resolves before handing off
    to the adapter — paths, not raw values, matching catraz's "secrets are
    file references" convention. `persistent_state_dir` is a writable
    per-repo directory, always mounted; an adapter opts into it only when
    its manifest declares `credentials.mode = "persistent"`."""

    auth_mode: str
    subscription_ro_dir: Path | None
    persistent_state_dir: Path | None
    api_key_file: Path | None
    api_key_env_fallback: str
    remote: bool


@dataclass(frozen=True)
class InstructionContext:
    """Input to `render_instructions`: the agent-neutral facts an adapter
    needs to render its instructions file (`CLAUDE.md`, `AGENTS.md`, …).
    `forge_rest_base` is a generic per-host rule, not one concrete URL: a
    literal `"<host>"` placeholder the agent substitutes with whichever git
    host it's actually talking to — one rule covers every endpoint."""

    forge_rest_base: str
    branch_prefixes: tuple[str, ...]
    warden_toml_path: Path


class AgentAdapter(Protocol):
    """The contract every `adapter.py` module implements at module scope:
    its top-level functions structurally satisfy this Protocol (callers
    `cast()` the imported module to `AgentAdapter`). Checked at runtime,
    for every registered adapter, by the Adapter-Conformance-Harness."""

    def prepare_home(self, home: Path, secrets: Secrets) -> None:
        """Write credential files and settings layout into the live (tmpfs)
        home. Never writes Forge or foreign-model credentials."""
        ...

    def command(self, argv: list[str]) -> list[str]:
        """Build the argv to `exec` for a one-off run, given the user's
        trailing arguments."""
        ...

    def environ(self, secrets: Secrets) -> dict[str, str]:
        """Extra environment variables the agent process needs (many CLIs
        read a key and a base URL/org from env — a single `api_key_env` is
        not enough, hence a full mapping here)."""
        ...

    def render_instructions(self, ctx: InstructionContext) -> tuple[Path, str]:
        """Return (destination path, rendered content) for the agent's
        instructions file — target *and* content, rendered per agent
        (namespace prefix, Warden REST base, curl examples), not merely
        placed from a static asset."""
        ...

    def remote_command(self) -> list[str] | None:
        """Argv for the long-lived remote-control daemon, or `None` if this
        agent/profile does not support it — callers must fail closed on
        `None`, never fall back to a half-configured daemon."""
        ...
