def test_migrate_moves_and_gitignores(tmp_path):
    (tmp_path / "claude").mkdir(); (tmp_path / ".env").write_text("DEV_UID=1000\n")
    from catraz import cli
    rc = cli.cmd_migrate(tmp_path, None, cli.Out(color=False))
    assert rc == 0
    assert (tmp_path / ".catraz/claude").is_dir() and (tmp_path / ".catraz/.env").exists()
    assert not (tmp_path / "claude").exists()
    assert ".catraz/" in (tmp_path / ".gitignore").read_text()
