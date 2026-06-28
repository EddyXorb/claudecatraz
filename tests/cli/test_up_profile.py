import types
import pytest

from catraz import cli, doctor, image
from catraz.commands import stack


def _mock_cmd_up(monkeypatch, tmp_path):
    """Stub every side-effecting collaborator cmd_up touches and record compose calls.

    cmd_up itself calls compose.run twice: once inside assert_invariants (a `config`
    call) and once for the real `up`. We no-op assert_invariants so only the `up`
    call reaches our recording compose_run stub. Returns the recording list."""
    (tmp_path / ".catraz").mkdir()
    # api_key mode → cmd_up's subscription auto-sync branch is skipped (no _run_sync).
    (tmp_path / ".catraz" / ".env").write_text("AUTH_MODE=api_key\n")

    calls = []
    monkeypatch.setattr(stack, "run_doctor", lambda *a, **k: doctor.Findings())
    monkeypatch.setattr(stack, "assert_real_dirs", lambda *a, **k: None)
    monkeypatch.setattr(stack, "assert_invariants", lambda *a, **k: None)
    monkeypatch.setattr(stack.auth, "write_auth_fragment", lambda root: None)
    monkeypatch.setattr(stack, "_wait_healthy", lambda *a, **k: None)
    monkeypatch.setattr(image, "resolve_base", lambda root: "catraz-base:test")
    import catraz.compose as compose_mod
    monkeypatch.setattr(compose_mod, "generate_resolved", lambda *a, **k: False)

    def fake_compose_run(root, args, **k):
        calls.append(list(args))
        return types.SimpleNamespace(returncode=0)
    monkeypatch.setattr(stack, "compose_run", fake_compose_run)
    return calls


def _args(remote, **overrides):
    ns = types.SimpleNamespace(
        print_only=False, build=False, pull=False,
        remote=remote, no_wait=True, timeout=1,
    )
    for k, v in overrides.items():
        setattr(ns, k, v)
    return ns


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


# ── B1: --dry-run side-effect free ──────────────────────────────────────────────

def test_dry_run_calls_compose_run_with_print_only(monkeypatch, tmp_path):
    """(a) compose_run called exactly once with print_only=True; no assert_invariants/run_doctor."""
    (tmp_path / ".catraz").mkdir()
    (tmp_path / ".catraz" / ".env").write_text("AUTH_MODE=api_key\n")

    compose_calls = []
    invariants_calls = []
    doctor_calls = []

    monkeypatch.setattr(stack.auth, "write_auth_fragment", lambda root: None)
    monkeypatch.setattr(stack, "assert_real_dirs", lambda root: None)
    monkeypatch.setattr(stack, "assert_invariants",
                        lambda *a, **k: invariants_calls.append(1))
    monkeypatch.setattr(stack, "run_doctor",
                        lambda *a, **k: doctor_calls.append(1) or doctor.Findings())

    def fake_compose_run(root, args, print_only=False, **k):
        compose_calls.append({"args": list(args), "print_only": print_only})
        return None

    monkeypatch.setattr(stack, "compose_run", fake_compose_run)

    rc = cli.cmd_up(tmp_path, _args(remote=False, print_only=True),
                    cli.Out(color=False))

    assert rc == 0
    assert len(compose_calls) == 1
    assert compose_calls[0]["print_only"] is True
    assert invariants_calls == [], "assert_invariants must NOT be called in dry-run"
    assert doctor_calls == [], "run_doctor must NOT be called in dry-run"


def test_dry_run_writes_fragment_for_fidelity(monkeypatch, tmp_path):
    """(b) Fragment is written before dry-run so the printed cmd includes -f .auth.compose.yml."""
    (tmp_path / ".catraz").mkdir()
    (tmp_path / ".catraz" / ".env").write_text("AUTH_MODE=api_key\n")
    frag_path = tmp_path / ".catraz" / ".auth.compose.yml"
    assert not frag_path.exists()

    # write_auth_fragment creates the file for real (use real auth, but stub the compose call)
    def real_write_frag(root):
        frag_path.write_text("# auth fragment\n")

    monkeypatch.setattr(stack.auth, "write_auth_fragment", real_write_frag)
    monkeypatch.setattr(stack, "assert_real_dirs", lambda *a, **k: None)
    monkeypatch.setattr(stack, "assert_invariants", lambda *a, **k: None)
    monkeypatch.setattr(stack, "compose_run", lambda *a, **k: None)

    cli.cmd_up(tmp_path, _args(remote=False, print_only=True), cli.Out(color=False))

    # Fragment must exist after the dry-run
    assert frag_path.exists(), "write_auth_fragment must be called before the print_only branch"


def test_dry_run_returns_ok_even_when_assert_invariants_would_fail(monkeypatch, tmp_path):
    """(c) EXIT_OK even when assert_invariants is patched to throw."""
    from catraz.errors import CliError, EXIT_DOCTOR
    (tmp_path / ".catraz").mkdir()
    (tmp_path / ".catraz" / ".env").write_text("AUTH_MODE=api_key\n")

    monkeypatch.setattr(stack.auth, "write_auth_fragment", lambda root: None)
    monkeypatch.setattr(stack, "assert_real_dirs", lambda *a, **k: None)
    monkeypatch.setattr(stack, "assert_invariants",
                        lambda *a, **k: (_ for _ in ()).throw(CliError("boom", EXIT_DOCTOR)))
    monkeypatch.setattr(stack, "compose_run", lambda *a, **k: None)

    rc = cli.cmd_up(tmp_path, _args(remote=False, print_only=True), cli.Out(color=False))
    assert rc == 0


# ── B2: BASE_IMAGE resolution ────────────────────────────────────────────────────

def test_up_remote_resolves_base_image(monkeypatch, tmp_path):
    """cmd_up --remote calls image.resolve_base."""
    calls = _mock_cmd_up(monkeypatch, tmp_path)
    resolve_calls = []
    monkeypatch.setattr(image, "resolve_base",
                        lambda root: resolve_calls.append(1) or "catraz-base:test")
    rc = cli.cmd_up(tmp_path, _args(remote=True), cli.Out(color=False))
    assert rc == 0
    assert resolve_calls, "resolve_base must be called for --remote"


def test_up_no_remote_does_not_resolve_base_image(monkeypatch, tmp_path):
    """cmd_up without --remote does NOT call image.resolve_base."""
    calls = _mock_cmd_up(monkeypatch, tmp_path)
    resolve_calls = []
    monkeypatch.setattr(image, "resolve_base",
                        lambda root: resolve_calls.append(1) or "catraz-base:test")
    rc = cli.cmd_up(tmp_path, _args(remote=False), cli.Out(color=False))
    assert rc == 0
    assert resolve_calls == [], "resolve_base must NOT be called without --remote"
