"""§05.2 — the Agent-Adapter contract shared by the generic entrypoint and
every ``assets/agents/<name>/adapter.py``.

This module is loaded **by path**, never by package import — exactly like
every other file under ``assets/`` (see ``catraz.paths.asset_root()`` and
``tests/container/conftest.py``'s ``ep`` fixture). Each per-agent
``layer.Dockerfile`` ``COPY``s it next to ``entrypoint.py`` *and* next to its
own ``adapter.py`` (flattened into one image directory — see
``assets/agents/claude/layer.Dockerfile``), so a plain ``import
agent_contract`` resolves at container start without importlib/entry_points
machinery. §06.2 anti-goal A2 ("kein dynamisches Plugin-Laden") holds: the
*build* already committed to exactly one adapter; nothing here loads code
from a `.catraz/` config path.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


def read_toml(path: Path) -> dict[str, Any]:
    """Parse a manifest (``agent.toml``) — the one shared TOML reader (ODR):
    both the in-container adapter (reading its own co-located manifest) and
    the host-side ``catraz.agents`` module (reading it for `init`/`doctor`)
    call this instead of duplicating ``tomllib`` plumbing."""
    return tomllib.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Secrets:
    """File-based secret material the generic entrypoint resolves *before*
    handing off to the adapter (§05.2) — paths, not raw values, matching
    catraz's "secrets are file references" convention (``doctor.SECRETS``).

    ``persistent_state_dir`` backs §05.6: a writable per-repo directory
    (``.catraz/state/<profile>/``, mounted read-write) an adapter may wire
    *selectively* into the live home when its manifest declares
    ``credentials.mode = "persistent"``. It is always present (mounted
    unconditionally so the same compose file serves every profile); whether
    an adapter uses it is its own decision, read from its own manifest.
    """
    auth_mode: str
    subscription_ro_dir: Path | None
    persistent_state_dir: Path | None
    api_key_file: Path | None
    api_key_env_fallback: str
    remote: bool


@dataclass(frozen=True)
class InstructionContext:
    """Input to ``render_instructions`` (§05.2): the agent-neutral facts an
    adapter needs to render its instructions file (``CLAUDE.md``,
    ``AGENTS.md``, …). The Forge REST base is an explicit field, not prose an
    adapter has to hardcode — "der REST-Draht des Agenten ist Teil des
    Vertrags, nicht Prosa-Zufall" (§05.2)."""
    forge_rest_base: str
    branch_prefixes: tuple[str, ...]
    warden_toml_path: Path


class AgentAdapter(Protocol):
    """The §05.2 contract every ``adapter.py`` module implements at module
    scope (a *module*, not a class — its top-level functions satisfy this
    Protocol structurally; callers ``cast()`` the imported module to
    ``AgentAdapter`` for static typing, see ``entrypoint._load_adapter``).

    Checked at runtime, for every registered adapter, by the
    Adapter-Conformance-Harness (§05.5,
    ``tests/container/test_adapter_conformance.py``).
    """

    def prepare_home(self, home: Path, secrets: Secrets) -> None:
        """Write credential files and settings layout into the live (tmpfs)
        home. Never writes Forge or foreign-model credentials (§05.5)."""
        ...

    def command(self, argv: list[str]) -> list[str]:
        """Build the argv to ``exec`` for a one-off run, given the user's
        trailing arguments."""
        ...

    def environ(self, secrets: Secrets) -> dict[str, str]:
        """Extra environment variables the agent process needs (many CLIs
        read a key *and* a base URL/org from env — a single ``api_key_env``
        is not enough, hence a full mapping here)."""
        ...

    def render_instructions(self, ctx: InstructionContext) -> tuple[Path, str]:
        """Return (destination path, rendered content) for the agent's
        instructions file — target *and* content, rendered per agent
        (namespace prefix, Warden REST base, curl examples), not merely
        placed from a static asset."""
        ...

    def remote_command(self) -> list[str] | None:
        """Argv for the long-lived remote-control daemon, or ``None`` if this
        agent/profile does not support it — callers must fail closed on
        ``None``, never fall back to a half-configured daemon."""
        ...
