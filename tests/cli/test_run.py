from pathlib import Path

import pytest

from catraz.commands import run


def test_oneoff_args_passthrough_and_tty() -> None:
    a = run._oneoff_args("src/foo", tty=False, sub="run", sub_args=["-p", "fix bug"])
    assert a[:4] == ["run", "--rm", "--no-deps", "-T"]
    assert "--build" not in a  # catraz builds the agent image itself
    assert "--workdir" in a and "/workspace/src/foo" in a
    # claude args appear verbatim after the entrypoint `run --` (the 2nd "run" token):
    assert a[a.index("run", 1) :] == ["run", "--", "-p", "fix bug"]


def test_oneoff_args_tty_omits_T_and_empty_workdir() -> None:
    a = run._oneoff_args("", tty=True, sub="run", sub_args=[])
    assert "-T" not in a
    assert "--build" not in a
    assert "/workspace" in a  # rstrip("/") → "/workspace"
    assert a[a.index("run", 1) :] == ["run", "--"]


def test_run_fails_closed_without_catraz(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # no .catraz here
    from catraz import errors, paths

    with pytest.raises(errors.CliError):
        paths.find_root()
