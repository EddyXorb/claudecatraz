import importlib.util
import shutil
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parents[2]
_CONTAINER = _REPO / "src/catraz/assets/container"
_CLAUDE_AGENT = _REPO / "src/catraz/assets/agents/claude"


def _stage_image_layout(dst: Path) -> None:
    """Recreate the flattened, built-image layout in a plain directory:
    entrypoint.py + agent_contract.py + git_routing.py next to
    agent_adapter.py/agent.toml/AGENT.md.tmpl for the claude profile, so
    entrypoint's own path-based imports resolve as they would in a real
    container."""
    shutil.copy2(_CONTAINER / "entrypoint.py", dst / "entrypoint.py")
    shutil.copy2(_CONTAINER / "agent_contract.py", dst / "agent_contract.py")
    shutil.copy2(_CONTAINER / "git_routing.py", dst / "git_routing.py")
    shutil.copy2(_CLAUDE_AGENT / "adapter.py", dst / "agent_adapter.py")
    shutil.copy2(_CLAUDE_AGENT / "agent.toml", dst / "agent.toml")
    shutil.copy2(_CLAUDE_AGENT / "AGENT.md.tmpl", dst / "AGENT.md.tmpl")


@pytest.fixture
def ep(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """The generic container entrypoint module, staged into an image-shaped
    directory and loaded by path (it is an asset, not a package import)."""
    staged = tmp_path_factory.mktemp("entrypoint-image")
    _stage_image_layout(staged)
    p = staged / "entrypoint.py"
    loader = SourceFileLoader("entrypoint", str(p))
    spec = importlib.util.spec_from_loader("entrypoint", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    # Register in sys.modules before exec so string annotations resolve.
    sys.modules[spec.name] = mod
    loader.exec_module(mod)
    return mod


class FakeAdapter:
    """A minimal stand-in satisfying the AgentAdapter contract, for tests
    that exercise the generic entrypoint orchestration without any
    claude-specific behaviour."""

    def __init__(
        self,
        *,
        remote_argv: list[str] | None = None,
        instructions_dest: Path | None = None,
        command_prefix: list[str] | None = None,
    ) -> None:
        self.prepared: list[tuple[Path, Any]] = []
        self.environ_calls: list[Any] = []
        self._remote_argv = remote_argv
        self._instructions_dest = instructions_dest
        self._command_prefix = command_prefix or ["fake-agent"]

    def prepare_home(self, home: Path, secrets: Any) -> None:
        self.prepared.append((home, secrets))

    def command(self, argv: list[str]) -> list[str]:
        return [*self._command_prefix, *argv]

    def environ(self, secrets: Any) -> dict[str, str]:
        self.environ_calls.append(secrets)
        return {}

    def render_instructions(self, ctx: Any) -> tuple[Path, str]:
        return self._instructions_dest or Path("/dev/null"), "fake instructions"

    def remote_command(self) -> list[str] | None:
        return self._remote_argv


@pytest.fixture
def fake_adapter_cls() -> type[FakeAdapter]:
    """The `FakeAdapter` class, handed out as a fixture rather than imported
    directly — test files live alongside this conftest.py but pytest's
    importlib import-mode does not add the directory to sys.path, so a bare
    `import conftest` from a sibling test module is not guaranteed to
    resolve."""
    return FakeAdapter


@pytest.fixture
def claude_adapter(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """The real claude adapter module, staged next to its own contract/manifest
    (same flattened layout) and loaded by path — used by adapter-specific
    tests and the conformance harness."""
    staged = tmp_path_factory.mktemp("claude-adapter")
    _stage_image_layout(staged)
    p = staged / "agent_adapter.py"
    loader = SourceFileLoader("agent_adapter", str(p))
    spec = importlib.util.spec_from_loader("agent_adapter", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    loader.exec_module(mod)
    return mod
