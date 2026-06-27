"""B2: cmd_run always resolves BASE_IMAGE before the compose call."""
import types
import pytest
from catraz import image
from catraz.commands import run as run_cmd
from catraz.ui import Out
from catraz.errors import EXIT_OK


def _mock_cmd_run(monkeypatch, tmp_path):
    """Stub every side-effecting collaborator cmd_run touches."""
    (tmp_path / ".catraz").mkdir()
    (tmp_path / ".catraz" / ".env").write_text("AUTH_MODE=api_key\n")

    compose_calls = []

    monkeypatch.setattr(run_cmd, "assert_real_dirs", lambda root: None)
    monkeypatch.setattr(run_cmd.auth, "write_auth_fragment", lambda root: None)
    monkeypatch.setattr(run_cmd, "assert_invariants", lambda root: None)
    monkeypatch.setattr(run_cmd, "_ensure_infra", lambda root, out: None)
    monkeypatch.setattr(image, "resolve_base", lambda root: "catraz-base:test")

    def fake_compose_run(root, args, check=True, extra_env=None, **k):
        compose_calls.append({"args": list(args), "extra_env": extra_env})
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(run_cmd, "compose_run", fake_compose_run)
    return compose_calls


def test_cmd_run_passes_base_image_to_compose(monkeypatch, tmp_path):
    """cmd_run must pass extra_env={"BASE_IMAGE": ...} to compose_run."""
    calls = _mock_cmd_run(monkeypatch, tmp_path)
    args = types.SimpleNamespace(claude_args=[])
    monkeypatch.chdir(tmp_path)
    rc = run_cmd.cmd_run(tmp_path, args, Out(color=False))
    assert rc == 0
    assert len(calls) == 1
    assert calls[0]["extra_env"] == {"BASE_IMAGE": "catraz-base:test"}
