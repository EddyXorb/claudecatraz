from pathlib import Path


def test_ensure_gitignore_appends_once(tmp_path: Path) -> None:
    from catraz import cli

    cli._ensure_gitignore(tmp_path)
    cli._ensure_gitignore(tmp_path)
    gi = (tmp_path / ".gitignore").read_text()
    assert gi.count(".catraz/") == 1


def test_ensure_gitignore_preserves_existing(tmp_path: Path) -> None:
    from catraz import cli

    (tmp_path / ".gitignore").write_text("node_modules/\n")
    cli._ensure_gitignore(tmp_path)
    lines = (tmp_path / ".gitignore").read_text().splitlines()
    assert "node_modules/" in lines and ".catraz/" in lines
