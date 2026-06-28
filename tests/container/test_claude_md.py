import pytest


def test_install_copies_from_ro(ep, tmp_path):
    home = tmp_path / ".claude"; (home/".ro").mkdir(parents=True)
    (home/".ro"/"CLAUDE.md").write_text("# memory\nrule")
    ep.install_claude_md(home)
    assert (home/"CLAUDE.md").read_text() == "# memory\nrule"


def test_install_missing_not_required_is_silent(ep, tmp_path, monkeypatch):
    home = tmp_path / ".claude"; home.mkdir()
    monkeypatch.delenv("REQUIRE_CLAUDE_FILE", raising=False)
    ep.install_claude_md(home)                      # no .ro/CLAUDE.md, not required
    assert not (home/"CLAUDE.md").exists()           # starts without user-memory, no error


def test_install_missing_required_exits(ep, tmp_path, monkeypatch):
    home = tmp_path / ".claude"; home.mkdir()
    monkeypatch.setenv("REQUIRE_CLAUDE_FILE", "true")
    with pytest.raises(SystemExit) as ei:
        ep.install_claude_md(home)
    assert "REQUIRE_CLAUDE_FILE" in str(ei.value) and "CLAUDE.md" in str(ei.value)
