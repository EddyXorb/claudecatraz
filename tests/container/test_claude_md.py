"""§05.2 `render_instructions`/`install_instructions`: the former (Claude-only,
static-file) `install_claude_md` is now two layers — a generic
`entrypoint.install_instructions` that fails closed on
REQUIRE_AGENT_INSTRUCTIONS, and the claude adapter's `render_instructions`
that renders this project's actual namespace prefix + Warden REST base into
the packaged template (not merely placing a static file)."""

from pathlib import Path
from typing import Any
import pytest


def _ctx(ep: Any, **overrides: Any) -> Any:
    base = dict(
        forge_rest_base="http://gitlab-warden:8080/api/v4",
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
    assert "http://gitlab-warden:8080/api/v4" in content
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
    assert dest.exists() and "gitlab-warden" in dest.read_text()


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
