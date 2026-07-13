"""`render_instructions`/`install_instructions`: a generic
`entrypoint.install_instructions` fails closed on REQUIRE_AGENT_INSTRUCTIONS,
and the claude adapter's `render_instructions` renders this project's actual
namespace prefix and Warden REST base into the packaged template."""

from pathlib import Path
from typing import Any
import pytest


def _ctx(ep: Any, **overrides: Any) -> Any:
    base = dict(
        # A generic per-host rule, not one concrete URL; never names the Warden container.
        forge_rest_base="http://<host>:8080/api/v4",
        branch_prefixes=("claude/",),
        warden_toml_path=Path("/etc/catraz/warden.toml"),
    )
    base.update(overrides)
    return ep.InstructionContext(**base)


# ── claude adapter: render_instructions ─────────────────────────────────────


def test_render_instructions_embeds_forge_base_and_prefix(
    ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ep.Path, "home", staticmethod(lambda: tmp_path))
    adapter = ep._load_adapter()
    dest, content = adapter.render_instructions(_ctx(ep))
    assert dest == tmp_path / ".claude" / "CLAUDE.md"
    assert "http://<host>:8080/api/v4" in content
    assert "gitlab-warden" not in content  # never leak the Warden's own container name
    assert "claude/" in content
    assert "__FORGE_REST_BASE__" not in content and "__BRANCH_PREFIX_EXAMPLE__" not in content


def test_render_instructions_uses_first_configured_prefix(
    ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ep.Path, "home", staticmethod(lambda: tmp_path))
    adapter = ep._load_adapter()
    _, content = adapter.render_instructions(_ctx(ep, branch_prefixes=("bot/", "agent/")))
    assert "bot/" in content


# ── generic entrypoint: install_instructions (fail-closed contract) ─────────


def test_install_writes_rendered_content(
    ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ep.Path, "home", staticmethod(lambda: tmp_path))
    adapter = ep._load_adapter()
    ep.install_instructions(adapter, _ctx(ep))
    dest = tmp_path / ".claude" / "CLAUDE.md"
    assert dest.exists() and "http://<host>:8080/api/v4" in dest.read_text(encoding="utf-8")


def test_install_missing_not_required_is_silent(
    ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("REQUIRE_AGENT_INSTRUCTIONS", raising=False)

    class _BrokenAdapter:
        def render_instructions(self, ctx: Any) -> Any:
            raise RuntimeError("template missing")

    ep.install_instructions(_BrokenAdapter(), _ctx(ep))  # must not raise


def test_install_missing_required_exits(
    ep: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REQUIRE_AGENT_INSTRUCTIONS", "true")

    class _BrokenAdapter:
        def render_instructions(self, ctx: Any) -> Any:
            raise RuntimeError("template missing")

    with pytest.raises(SystemExit) as ei:
        ep.install_instructions(_BrokenAdapter(), _ctx(ep))
    assert "template missing" in str(ei.value)
