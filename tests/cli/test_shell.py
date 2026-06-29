from catraz.commands import run as run_cmd


def test_shell_oneoff_default_bash() -> None:
    a = run_cmd._oneoff_args("", tty=False, sub="exec", sub_args=[])
    assert a[:5] == ["run", "--rm", "--no-deps", "--build", "-T"]
    assert a[-3:] == ["claude-dev-env", "exec", "--"]    # empty → entrypoint defaults to bash


def test_shell_oneoff_passthrough() -> None:
    a = run_cmd._oneoff_args("src", tty=True, sub="exec", sub_args=["ls", "-la"])
    assert "-T" not in a and "/workspace/src" in a and "--build" in a
    assert a[-5:] == ["claude-dev-env", "exec", "--", "ls", "-la"]
