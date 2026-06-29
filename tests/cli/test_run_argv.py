"""Option A: `run`'s tail is opaque to argparse, so `run -p x` needs no `--`.

cli._split_run slices argv at the `run` token; main() hands the verbatim tail to
the run handler. A lone -h/--help after `run` still shows catraz's own help."""
from pathlib import Path

import pytest

from catraz import cli


@pytest.mark.parametrize("argv, head, tail", [
    (["run", "-p", "x"], ["run"], ["-p", "x"]),              # leading flag, no `--`
    (["run", "shell", "ls", "-la"], ["run"], ["shell", "ls", "-la"]),
    (["run"], ["run"], []),                                  # bare → interactive claude
    (["-C", "/tmp", "run", "-p", "x"], ["-C", "/tmp", "run"], ["-p", "x"]),
    (["-C", "run", "status"], ["-C", "run", "status"], None),  # dir literally named `run`
    (["status"], ["status"], None),
    ([], [], None),
    (["--version"], ["--version"], None),
])
def test_split_run(argv: list[str], head: list[str], tail: list[str] | None) -> None:
    assert cli._split_run(argv) == (head, tail)


@pytest.mark.parametrize("argv, expected", [
    (["run", "-p", "x"], ["-p", "x"]),
    (["run", "shell", "ls", "-la"], ["shell", "ls", "-la"]),
    (["run"], []),
    (["run", "claude-remote"], ["claude-remote"]),
    (["run", "--", "--help"], ["--", "--help"]),            # `--help` reaches claude
])
def test_main_hands_opaque_tail_to_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    argv: list[str], expected: list[str],
) -> None:
    (tmp_path / ".catraz").mkdir()
    monkeypatch.setattr("catraz.paths.find_root", lambda x=None: tmp_path)
    seen: dict[str, object] = {}
    monkeypatch.setitem(cli.HANDLERS, "run",
                        lambda root, args, out: seen.update(ca=args.claude_args) or 0)
    assert cli.main(argv) == 0
    assert seen["ca"] == expected


@pytest.mark.parametrize("argv", [["run", "--help"], ["run", "-h"]])
def test_run_help_shows_catraz_help(capsys: pytest.CaptureFixture[str], argv: list[str]) -> None:
    with pytest.raises(SystemExit) as e:
        cli.main(argv)
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "modes:" in out and "examples:" in out
