from catraz import cli

def test_run_args_passthrough_and_tty():
    a = cli._local_run_args("src/foo", tty=False, claude_args=["-p", "fix bug"])
    assert a[:4] == ["run", "--rm", "--no-deps", "-T"]
    assert "--workdir" in a and "/workspace/src/foo" in a
    # claude args appear verbatim after 'local --':
    assert a[a.index("local"):] == ["local", "--", "-p", "fix bug"]

def test_run_args_tty_omits_T_and_empty_workdir():
    a = cli._local_run_args("", tty=True, claude_args=[])
    assert "-T" not in a
    assert "/workspace" in a                       # rstrip("/") → "/workspace"
    assert a[a.index("local"):] == ["local", "--"]

def test_local_fails_closed_without_catraz(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                     # no .catraz here
    import pytest
    from catraz import paths, errors
    with pytest.raises(errors.CliError):
        paths.find_root()
