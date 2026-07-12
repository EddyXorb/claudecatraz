from pathlib import Path

import pytest

from catraz import paths
from catraz.egress_allowlist import (
    DomainEntry,
    agent_block,
    classify_domains,
    domain_covered,
    upsert_agent_block,
)

FIXTURE = """# shipped header comment
.anthropic.com
.claude.ai

# --- Package registries ---
.npmjs.org
files.pythonhosted.org
"""


@pytest.fixture
def shipped_allowlist(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return (paths.asset_root() / "assets" / "config" / "allowlist.txt").read_text()


def test_agent_block_none_before_confirmation() -> None:
    assert agent_block(FIXTURE, "claude") is None


def test_upsert_append_leaves_prior_content_untouched() -> None:
    result = upsert_agent_block(FIXTURE, "claude", ("example.com", ".foo.com"))
    assert result.startswith(FIXTURE.rstrip("\n"))
    assert "# agent:claude begin" in result
    assert "# agent:claude end" in result
    assert agent_block(result, "claude") == ("example.com", ".foo.com")


def test_upsert_replace_in_place_is_position_stable() -> None:
    after_append = upsert_agent_block(FIXTURE, "claude", ("example.com",))
    replaced = upsert_agent_block(after_append, "claude", ("other.com", ".bar.com"))
    assert agent_block(replaced, "claude") == ("other.com", ".bar.com")
    # content before and after the block is byte-stable across the replace
    assert replaced.split("# agent:claude begin")[0] == after_append.split("# agent:claude begin")[0]
    assert replaced.split("# agent:claude end")[1] == after_append.split("# agent:claude end")[1]


def test_upsert_empty_domains_removes_block_cleanly() -> None:
    after_append = upsert_agent_block(FIXTURE, "claude", ("example.com",))
    removed = upsert_agent_block(after_append, "claude", ())
    assert removed == FIXTURE
    assert agent_block(removed, "claude") is None


def test_upsert_empty_domains_on_no_block_is_noop() -> None:
    assert upsert_agent_block(FIXTURE, "claude", ()) == FIXTURE


def test_agent_block_returns_confirmed_tuple_in_file_order() -> None:
    result = upsert_agent_block(FIXTURE, "claude", ("z.com", "a.com", ".m.com"))
    assert agent_block(result, "claude") == ("z.com", "a.com", ".m.com")


def test_domain_covered_exact_match() -> None:
    assert domain_covered(FIXTURE, "files.pythonhosted.org") is True


def test_domain_covered_suffix_match() -> None:
    assert domain_covered(FIXTURE, "registry.npmjs.org") is True
    assert domain_covered(FIXTURE, "npmjs.org") is True


def test_domain_covered_negative() -> None:
    assert domain_covered(FIXTURE, "evil.com") is False


def test_domain_covered_ignores_comment_lines() -> None:
    text = "# see evil.com for details\n.safe.com\n"
    assert domain_covered(text, "evil.com") is False


def test_domain_covered_against_shipped_allowlist(shipped_allowlist: str) -> None:
    assert domain_covered(shipped_allowlist, "api.anthropic.com") is True
    assert domain_covered(shipped_allowlist, "anthropic.com") is True
    assert domain_covered(shipped_allowlist, "evil.com") is False


def test_classify_domains_baseline_vs_agent_block_vs_manual() -> None:
    with_block = upsert_agent_block(FIXTURE, "claude", ("agent-only.com",))
    with_manual = with_block + "manually-added.com\n"
    result = classify_domains(with_manual, FIXTURE)
    by_entry = {e.entry: e.provenance for e in result}
    assert by_entry[".anthropic.com"] == "baseline"
    assert by_entry[".npmjs.org"] == "baseline"
    assert by_entry["agent-only.com"] == "agent:claude"
    assert by_entry["manually-added.com"] == "manual"


def test_classify_domains_against_shipped_baseline(shipped_allowlist: str) -> None:
    result = classify_domains(shipped_allowlist, shipped_allowlist)
    assert all(e.provenance == "baseline" for e in result)
    assert DomainEntry(".anthropic.com", "baseline") in result

    synthesized = upsert_agent_block(shipped_allowlist, "claude", ("extra.example.com",))
    result2 = classify_domains(synthesized, shipped_allowlist)
    by_entry = {e.entry: e.provenance for e in result2}
    assert by_entry["extra.example.com"] == "agent:claude"
    assert by_entry[".anthropic.com"] == "baseline"

    hand_appended = synthesized + "hand-added.example.com\n"
    result3 = classify_domains(hand_appended, shipped_allowlist)
    by_entry3 = {e.entry: e.provenance for e in result3}
    assert by_entry3["hand-added.example.com"] == "manual"
