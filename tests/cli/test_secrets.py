"""Commit 11.2 — .catraz/secrets/ for GitLab tokens."""
import argparse
import stat
import types

import pytest

from catraz.commands import setup
from catraz.doctor import run_doctor, _doctor_fix, Findings, SECRETS
from catraz.envfile import load_env
from catraz.ui import Out


def _make_root(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    cat = root / ".catraz"
    cat.mkdir()
    (cat / "config").mkdir()
    (cat / "config" / "warden.toml").write_text('allowed_projects = ["group/sub/proj"]\n')
    (cat / ".env").write_text("DEV_UID=1000\nAUTH_MODE=subscription\n")
    return root


def _yes_args():
    return argparse.Namespace(
        yes=True, force=False, skip_sync=False,
        dir=None, no_color=True, print_only=False,
    )


def test_cmd_init_creates_secret_files_even_blank(tmp_path, monkeypatch):
    """cmd_init --yes creates secrets/ at 0700 and both token files at 0600, even if blank."""
    root = _make_root(tmp_path)

    monkeypatch.setattr("catraz.commands.setup._run_sync", lambda *a, **kw: None)
    monkeypatch.setattr("catraz.commands.setup.run_doctor",
                        lambda *a, **kw: types.SimpleNamespace(items=[]))
    monkeypatch.setattr("catraz.commands.setup.print_findings",
                        lambda *a, **kw: (0, 0))

    out = Out(color=False)
    setup.cmd_init(root, _yes_args(), out)

    secrets_dir = root / ".catraz" / "secrets"
    assert secrets_dir.is_dir()
    assert stat.S_IMODE(secrets_dir.stat().st_mode) == 0o700

    for filename, _, _ in SECRETS:
        p = secrets_dir / filename
        assert p.exists(), f"missing: {p}"
        assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_cmd_init_writes_token_via_getpass(tmp_path, monkeypatch):
    """cmd_init (interactive) writes the token value to the secret file at 0600."""
    root = _make_root(tmp_path)

    secrets = iter(["glpat-readtoken", "glpat-writetoken"])
    monkeypatch.setattr("catraz.commands.setup.getpass.getpass", lambda prompt: next(secrets))
    monkeypatch.setattr("builtins.input", lambda prompt: "")  # GITLAB_URL + projects prompts
    monkeypatch.setattr("catraz.commands.setup._run_sync", lambda *a, **kw: None)
    monkeypatch.setattr("catraz.commands.setup.run_doctor",
                        lambda *a, **kw: types.SimpleNamespace(items=[]))
    monkeypatch.setattr("catraz.commands.setup.print_findings",
                        lambda *a, **kw: (0, 0))

    args = argparse.Namespace(
        yes=False, force=False, skip_sync=False,
        dir=None, no_color=True, print_only=False,
    )
    out = Out(color=False)
    setup.cmd_init(root, args, out)

    secrets_dir = root / ".catraz" / "secrets"
    assert (secrets_dir / "gitlab_read_token").read_text().strip() == "glpat-readtoken"
    assert (secrets_dir / "gitlab_write_token").read_text().strip() == "glpat-writetoken"
    for filename, _, _ in SECRETS:
        assert stat.S_IMODE((secrets_dir / filename).stat().st_mode) == 0o600


def test_doctor_bad_on_empty_file(tmp_path):
    """doctor reports bad for each token file that exists but is empty."""
    root = _make_root(tmp_path)
    secrets_dir = root / ".catraz" / "secrets"
    secrets_dir.mkdir(mode=0o700)
    for filename, _, _ in SECRETS:
        p = secrets_dir / filename
        p.write_text("")
        p.chmod(0o600)

    f = run_doctor(root, only=["tokens"])
    bad_msgs = [i[2] for i in f.items if i[0] == "bad"]
    for filename, _, _ in SECRETS:
        assert any(filename in m for m in bad_msgs), f"expected bad for {filename!r}"


def test_doctor_ok_on_non_empty(tmp_path, monkeypatch):
    """doctor reports ok when both secret files are non-empty (probe skipped in unit tests)."""
    import catraz.doctor as doc
    monkeypatch.setattr(doc, "_probe_gitlab_tokens", lambda *a, **kw: None)

    root = _make_root(tmp_path)
    secrets_dir = root / ".catraz" / "secrets"
    secrets_dir.mkdir(mode=0o700)
    for filename, _, _ in SECRETS:
        p = secrets_dir / filename
        p.write_text("glpat-xxxxxxxtoken")
        p.chmod(0o600)

    f = run_doctor(root, only=["tokens"])
    assert not any(i[0] == "bad" for i in f.items)
    assert any(i[0] == "ok" and "both GitLab tokens" in i[2] for i in f.items)


def test_doctor_fix_creates_secrets_dir_and_files(tmp_path):
    """doctor --fix always creates secrets/ dir (0700) and empty token files (0600)."""
    root = _make_root(tmp_path)
    env = load_env(root / ".catraz" / ".env")
    _doctor_fix(root, env)

    secrets_dir = root / ".catraz" / "secrets"
    assert secrets_dir.is_dir()
    assert stat.S_IMODE(secrets_dir.stat().st_mode) == 0o700
    for filename, _, _ in SECRETS:
        p = secrets_dir / filename
        assert p.exists(), f"missing: {p}"
        assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_cmd_init_yes_reads_tokens_from_env(tmp_path, monkeypatch):
    """--yes writes token env vars to secret files at 0600."""
    root = _make_root(tmp_path)
    monkeypatch.setenv("GITLAB_READ_TOKEN", "glpat-read-from-env")
    monkeypatch.setenv("GITLAB_WRITE_TOKEN", "glpat-write-from-env")
    monkeypatch.setattr("catraz.commands.setup._run_sync", lambda *a, **kw: None)
    monkeypatch.setattr("catraz.commands.setup.run_doctor",
                        lambda *a, **kw: types.SimpleNamespace(items=[]))
    monkeypatch.setattr("catraz.commands.setup.print_findings",
                        lambda *a, **kw: (0, 0))

    setup.cmd_init(root, _yes_args(), Out(color=False))

    secrets_dir = root / ".catraz" / "secrets"
    assert (secrets_dir / "gitlab_read_token").read_text() == "glpat-read-from-env"
    assert (secrets_dir / "gitlab_write_token").read_text() == "glpat-write-from-env"
    for filename, _, _ in SECRETS:
        assert stat.S_IMODE((secrets_dir / filename).stat().st_mode) == 0o600


def test_cmd_init_yes_persists_warden_projects_from_env(tmp_path, monkeypatch):
    """--yes writes WARDEN_ALLOWED_PROJECTS from env to .env."""
    root = _make_root(tmp_path)
    monkeypatch.setenv("WARDEN_ALLOWED_PROJECTS", "group/proj-a,group/proj-b")
    monkeypatch.setattr("catraz.commands.setup._run_sync", lambda *a, **kw: None)
    monkeypatch.setattr("catraz.commands.setup.run_doctor",
                        lambda *a, **kw: types.SimpleNamespace(items=[]))
    monkeypatch.setattr("catraz.commands.setup.print_findings",
                        lambda *a, **kw: (0, 0))

    setup.cmd_init(root, _yes_args(), Out(color=False))

    env = load_env(root / ".catraz" / ".env")
    assert env.get("WARDEN_ALLOWED_PROJECTS") == "group/proj-a,group/proj-b"


def test_doctor_fix_does_not_overwrite_existing_token(tmp_path):
    """doctor --fix leaves an already-populated token file unchanged."""
    root = _make_root(tmp_path)
    secrets_dir = root / ".catraz" / "secrets"
    secrets_dir.mkdir(mode=0o700)
    first_file = SECRETS[0][0]
    (secrets_dir / first_file).write_text("existing-token")
    (secrets_dir / first_file).chmod(0o600)

    env = load_env(root / ".catraz" / ".env")
    _doctor_fix(root, env)

    assert (secrets_dir / first_file).read_text() == "existing-token"
