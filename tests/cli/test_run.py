from catraz import cli

def test_oneoff_args_passthrough_and_tty():
    a = cli._oneoff_args("src/foo", tty=False, claude_args=["-p", "fix bug"])
    assert a[:4] == ["run", "--rm", "--no-deps", "-T"]
    assert "--workdir" in a and "/workspace/src/foo" in a
    # claude args appear verbatim after the entrypoint `run --` (the 2nd "run" token):
    assert a[a.index("run", 1):] == ["run", "--", "-p", "fix bug"]

def test_oneoff_args_tty_omits_T_and_empty_workdir():
    a = cli._oneoff_args("", tty=True, claude_args=[])
    assert "-T" not in a
    assert "/workspace" in a                       # rstrip("/") → "/workspace"
    assert a[a.index("run", 1):] == ["run", "--"]

def test_run_fails_closed_without_catraz(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                     # no .catraz here
    import pytest
    from catraz import paths, errors
    with pytest.raises(errors.CliError):
        paths.find_root()
