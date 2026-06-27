import types

from catraz import cli, doctor


def _mock_cmd_up(monkeypatch, tmp_path):
    """Stub every side-effecting collaborator cmd_up touches and record compose calls.

    cmd_up itself calls compose.run twice: once inside assert_invariants (a `config`
    call) and once for the real `up`. We no-op assert_invariants so only the `up`
    call reaches our recording compose_run stub. Returns the recording list."""
    (tmp_path / ".catraz").mkdir()
    # api_key mode → cmd_up's subscription auto-sync branch is skipped (no _run_sync).
    (tmp_path / ".catraz" / ".env").write_text("AUTH_MODE=api_key\n")

    calls = []
    monkeypatch.setattr(cli, "run_doctor", lambda *a, **k: doctor.Findings())
    monkeypatch.setattr(cli, "assert_real_dirs", lambda root: None)
    monkeypatch.setattr(cli, "assert_invariants", lambda root: None)
    monkeypatch.setattr(cli.auth, "write_auth_fragment", lambda root: None)
    monkeypatch.setattr(cli, "_wait_healthy", lambda *a, **k: None)

    def fake_compose_run(root, args, **k):
        calls.append(list(args))
        return types.SimpleNamespace(returncode=0)
    monkeypatch.setattr(cli, "compose_run", fake_compose_run)
    return calls


def _args(remote):
    return types.SimpleNamespace(
        print_only=False, build=False, pull=False,
        remote=remote, no_wait=True, timeout=1,
    )


def _up_call(calls):
    return next(a for a in calls if "up" in a)


def test_up_without_remote_has_no_profile(monkeypatch, tmp_path):
    calls = _mock_cmd_up(monkeypatch, tmp_path)
    rc = cli.cmd_up(tmp_path, _args(remote=False), cli.Out(color=False))
    assert rc == 0
    up = _up_call(calls)
    assert "--profile" not in up


def test_up_remote_adds_profile_before_up(monkeypatch, tmp_path):
    calls = _mock_cmd_up(monkeypatch, tmp_path)
    rc = cli.cmd_up(tmp_path, _args(remote=True), cli.Out(color=False))
    assert rc == 0
    up = _up_call(calls)
    assert "--profile" in up and "remote" in up
    assert up.index("--profile") < up.index("up")
