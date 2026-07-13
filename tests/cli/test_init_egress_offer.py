"""Init egress offer: manifest domains are offered one by one in the
interactive path, written only inside the profile's marked block, never on
decline, and never at all under `--yes` (the never-auto-merge boundary)."""

import argparse
import shutil
import types
from pathlib import Path

import pytest

from catraz.agents import AgentManifest
from catraz.commands import setup
from catraz.commands.setup._wizard_interactive import _prompt_egress_offer
from catraz.egress_allowlist import agent_block
from catraz.paths import asset_root
from catraz.ui import Out

BASELINE = "# baseline\n.anthropic.com\n.claude.ai\n.claude.com\n"


def _manifest(domains: tuple[str, ...]) -> AgentManifest:
    return AgentManifest(
        name="claude",
        command="claude",
        subscription_source="",
        api_key_env="ANTHROPIC_API_KEY",
        credentials_mode="persistent",
        remote_allowed=False,
        debug_flag="--debug-file",
        egress_domains=domains,
    )


def _make_root(tmp_path: Path, allowlist_text: str = BASELINE) -> Path:
    root = tmp_path / "proj"
    (root / ".catraz" / "config").mkdir(parents=True)
    (root / ".catraz" / ".env").write_text("AGENT_PROFILE=claude\n")
    (root / ".catraz" / "config" / "allowlist.txt").write_text(allowlist_text)
    return root


def _feed_answers(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> None:
    it = iter(answers)

    def _input(prompt: object) -> str:
        try:
            return next(it)
        except StopIteration:
            return ""

    monkeypatch.setattr("builtins.input", _input)


def _use_manifest(monkeypatch: pytest.MonkeyPatch, domains: tuple[str, ...]) -> None:
    monkeypatch.setattr("catraz.agents.load_manifest", lambda profile: _manifest(domains))


def test_shipped_accept_writes_only_inside_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_root(tmp_path)
    _use_manifest(monkeypatch, ("extra.example.com",))
    _feed_answers(monkeypatch, ["y"])
    _prompt_egress_offer(root, root / ".catraz", Out(color=False))
    text = (root / ".catraz" / "config" / "allowlist.txt").read_text()
    assert agent_block(text, "claude") == ("extra.example.com",)
    before, _, after = text.partition("extra.example.com")
    assert "# agent:claude begin" in before
    assert "# agent:claude end" in after


def test_shipped_decline_leaves_allowlist_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_root(tmp_path)
    original = (root / ".catraz" / "config" / "allowlist.txt").read_text()
    _use_manifest(monkeypatch, ("extra.example.com",))
    _feed_answers(monkeypatch, ["n"])
    _prompt_egress_offer(root, root / ".catraz", Out(color=False))
    assert (root / ".catraz" / "config" / "allowlist.txt").read_text() == original
    assert agent_block(original, "claude") is None


def test_covered_domain_is_not_offered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _make_root(tmp_path)
    original = (root / ".catraz" / "config" / "allowlist.txt").read_text()
    # .anthropic.com already covers this; there is nothing to offer.
    _use_manifest(monkeypatch, ("api.anthropic.com",))

    def _fail_input(prompt: object) -> str:
        raise AssertionError("a covered domain must not be prompted")

    monkeypatch.setattr("builtins.input", _fail_input)
    _prompt_egress_offer(root, root / ".catraz", Out(color=False))
    assert (root / ".catraz" / "config" / "allowlist.txt").read_text() == original


def test_out_of_tree_gate_declined_writes_nothing_and_skips_per_domain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_root(tmp_path)
    original = (root / ".catraz" / "config" / "allowlist.txt").read_text()
    monkeypatch.setattr("catraz.agents.SHIPPED_PROFILES", frozenset())
    _use_manifest(monkeypatch, ("extra.example.com",))
    prompts: list[str] = []

    def _input(prompt: object) -> str:
        prompts.append(str(prompt))
        return "n"  # decline the whole-set diff gate

    monkeypatch.setattr("builtins.input", _input)
    _prompt_egress_offer(root, root / ".catraz", Out(color=False))
    assert (root / ".catraz" / "config" / "allowlist.txt").read_text() == original
    assert not any("allow extra.example.com?" in p for p in prompts)


def test_out_of_tree_gate_accepted_then_per_domain_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_root(tmp_path)
    monkeypatch.setattr("catraz.agents.SHIPPED_PROFILES", frozenset())
    _use_manifest(monkeypatch, ("extra.example.com",))
    _feed_answers(monkeypatch, ["y", "y"])  # gate yes, then per-domain yes
    _prompt_egress_offer(root, root / ".catraz", Out(color=False))
    text = (root / ".catraz" / "config" / "allowlist.txt").read_text()
    assert agent_block(text, "claude") == ("extra.example.com",)


def test_hand_deleted_domain_is_reoffered_not_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_root(tmp_path)
    allow = root / ".catraz" / "config" / "allowlist.txt"
    _use_manifest(monkeypatch, ("extra.example.com",))
    _feed_answers(monkeypatch, ["y"])
    _prompt_egress_offer(root, root / ".catraz", Out(color=False))
    assert agent_block(allow.read_text(), "claude") == ("extra.example.com",)

    # Operator hand-deletes the domain from inside the block, leaving it empty.
    allow.write_text("# baseline\n.anthropic.com\n.claude.ai\n.claude.com\n")
    prompts: list[str] = []

    def _input(prompt: object) -> str:
        prompts.append(str(prompt))
        return "n"  # re-offered, but declined this time

    monkeypatch.setattr("builtins.input", _input)
    _prompt_egress_offer(root, root / ".catraz", Out(color=False))
    assert any("allow extra.example.com?" in p for p in prompts), (
        "deleted domain must be re-offered"
    )
    assert agent_block(allow.read_text(), "claude") is None


# --yes never offers or adds — the never-auto-merge guarantee.


def _yes_args() -> argparse.Namespace:
    return argparse.Namespace(
        yes=True, force=False, skip_sync=False, dir=None, no_color=True, print_only=False
    )


def _cmd_init_root(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    cat = root / ".catraz"
    cat.mkdir()
    (cat / "config").mkdir()
    shipped = asset_root() / "assets" / "config" / "warden.toml"
    dst = cat / "config" / "warden.toml"
    if shipped.exists():
        shutil.copy2(shipped, dst)
    else:
        dst.write_text('branch_prefix = "claude/"\nallowed_projects = [""]\n')
    (cat / ".env").write_text("DEV_UID=1000\nAUTH_MODE=subscription\n")
    return root


def test_yes_never_adds_an_agent_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _cmd_init_root(tmp_path)
    monkeypatch.setattr("catraz.commands.setup._run_sync", lambda *a, **kw: None)
    monkeypatch.setattr(
        "catraz.commands.setup.run_doctor", lambda *a, **kw: types.SimpleNamespace(items=[])
    )
    monkeypatch.setattr("catraz.commands.setup.print_findings", lambda *a, **kw: (0, 0))
    # Even a manifest with uncovered egress domains must not be merged under --yes.
    _use_manifest(monkeypatch, ("extra.example.com",))
    setup.cmd_init(root, _yes_args(), Out(color=False))
    allow = (root / ".catraz" / "config" / "allowlist.txt").read_text()
    shipped = (asset_root() / "assets" / "config" / "allowlist.txt").read_text()
    assert allow == shipped
    assert "# agent:" not in allow
