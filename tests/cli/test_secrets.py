"""Commit 11.2 — .catraz/secrets/ for GitLab tokens."""

import argparse
import stat
import types
from pathlib import Path

import pytest

from catraz.commands import setup
from catraz.doctor import run_doctor, _doctor_fix, SECRETS
from catraz.envfile import load_env
from catraz.ui import Out


def _make_root(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    cat = root / ".catraz"
    cat.mkdir()
    (cat / "config").mkdir()
    (cat / "config" / "warden.toml").write_text('allowed_projects = ["group/sub/proj"]\n')
    (cat / ".env").write_text("DEV_UID=1000\nAUTH_MODE=subscription\n")
    return root


def _yes_args() -> argparse.Namespace:
    return argparse.Namespace(
        yes=True,
        force=False,
        skip_sync=False,
        dir=None,
        no_color=True,
        print_only=False,
    )


def test_cmd_init_creates_secret_files_even_blank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cmd_init --yes creates secrets/ at 0700 and both token files at 0600, even if blank.

    With no token env vars the wizard infers GITLAB_MODE=off and writes it to .env.
    """
    root = _make_root(tmp_path)

    monkeypatch.setattr("catraz.commands.setup._run_sync", lambda *a, **kw: None)
    monkeypatch.setattr(
        "catraz.commands.setup.run_doctor",
        lambda *a, **kw: types.SimpleNamespace(items=[]),
    )
    monkeypatch.setattr("catraz.commands.setup.print_findings", lambda *a, **kw: (0, 0))

    out = Out(color=False)
    setup.cmd_init(root, _yes_args(), out)

    secrets_dir = root / ".catraz" / "secrets"
    assert secrets_dir.is_dir()
    assert stat.S_IMODE(secrets_dir.stat().st_mode) == 0o700

    for filename, _, _ in SECRETS:
        p = secrets_dir / filename
        assert p.exists(), f"missing: {p}"
        assert stat.S_IMODE(p.stat().st_mode) == 0o600

    # GITLAB_MODE must be written to .env (inferred as "off" — no tokens provided)
    env = load_env(root / ".catraz" / ".env")
    assert env.get("GITLAB_MODE") == "off"


def test_cmd_init_writes_token_via_getpass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """cmd_init (interactive) writes the token value to the secret file at 0600.

    The interactive wizard uses out.secret() (in ui.py) which calls getpass internally,
    so we patch catraz.ui.getpass.getpass rather than catraz.commands.setup.getpass.
    Empty input() returns defaults for choice/ask prompts.
    """
    root = _make_root(tmp_path)

    secrets = iter(["glpat-readtoken", "glpat-writetoken"])
    # out.secret() imports getpass locally and calls getpass.getpass(); patch at the module level
    monkeypatch.setattr("getpass.getpass", lambda prompt: next(secrets))
    # out.choice() and out.ask() use input(); "" picks the default each time
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    monkeypatch.setattr("catraz.commands.setup._run_sync", lambda *a, **kw: None)
    monkeypatch.setattr(
        "catraz.commands.setup.run_doctor",
        lambda *a, **kw: types.SimpleNamespace(items=[]),
    )
    monkeypatch.setattr("catraz.commands.setup.print_findings", lambda *a, **kw: (0, 0))

    args = argparse.Namespace(
        yes=False,
        force=False,
        skip_sync=False,
        dir=None,
        no_color=True,
        print_only=False,
    )
    out = Out(color=False)
    setup.cmd_init(root, args, out)

    secrets_dir = root / ".catraz" / "secrets"
    assert (secrets_dir / "gitlab_read_token").read_text().strip() == "glpat-readtoken"
    assert (secrets_dir / "gitlab_write_token").read_text().strip() == "glpat-writetoken"
    for filename, _, _ in SECRETS:
        assert stat.S_IMODE((secrets_dir / filename).stat().st_mode) == 0o600


def test_doctor_fix_on_fresh_root_creates_catraz(tmp_path: Path) -> None:
    """_doctor_fix on a project where .catraz/ does not exist yet must not crash.

    Regression: the 0700 secrets dirs are created with mode= (not parents=), so
    .catraz/ itself has to be created first — otherwise the first mkdir raises
    FileNotFoundError on a fresh init.
    """
    root = tmp_path / "fresh"
    root.mkdir()
    assert not (root / ".catraz").exists()

    _doctor_fix(root, {"DEV_UID": "1000", "AUTH_MODE": "subscription"})

    secrets_dir = root / ".catraz" / "secrets"
    claude_dir = secrets_dir / "claude"
    assert secrets_dir.is_dir()
    assert claude_dir.is_dir()
    assert stat.S_IMODE(secrets_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(claude_dir.stat().st_mode) == 0o700


def test_doctor_warns_endpoint_closed_on_empty_grouped_files(tmp_path: Path) -> None:
    """§08-multi-target Step 06: an [[git.endpoint]] with no token in the grouped
    read_tokens/write_tokens files warns "closed" — never a failing (bad) exit;
    the Warden, not doctor, is the side that enforces fail-closed."""
    root = _make_root(tmp_path)
    (root / ".catraz" / "config" / "warden.toml").write_text(
        '[[git.endpoint]]\nhost = "gitlab.com"\ntype = "gitlab"\n'
    )
    secrets_dir = root / ".catraz" / "secrets"
    secrets_dir.mkdir(mode=0o700)
    for filename in ("read_tokens", "write_tokens"):
        p = secrets_dir / filename
        p.write_text("")
        p.chmod(0o600)

    f = run_doctor(root, only=["tokens"])
    assert not any(i[0] == "bad" for i in f.items)
    assert any(i[0] == "warn" and "gitlab.com" in i[2] and "closed" in i[2] for i in f.items)


def test_doctor_ok_on_non_empty_grouped_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """doctor reports ok when a configured endpoint has both tokens set (probe
    skipped in unit tests)."""
    import catraz.doctor as doc

    monkeypatch.setattr(doc, "_probe_gitlab_tokens", lambda *a, **kw: None)

    root = _make_root(tmp_path)
    (root / ".catraz" / "config" / "warden.toml").write_text(
        '[[git.endpoint]]\nhost = "gitlab.com"\ntype = "gitlab"\n'
    )
    secrets_dir = root / ".catraz" / "secrets"
    secrets_dir.mkdir(mode=0o700)
    (secrets_dir / "read_tokens").write_text("gitlab.com glpat-xxxxxxxtoken\n")
    (secrets_dir / "read_tokens").chmod(0o600)
    (secrets_dir / "write_tokens").write_text("gitlab.com glpat-yyyyyyytoken\n")
    (secrets_dir / "write_tokens").chmod(0o600)

    f = run_doctor(root, only=["tokens"])
    assert not any(i[0] == "bad" for i in f.items)
    assert any(i[0] == "ok" and "gitlab.com" in i[2] and "read-write" in i[2] for i in f.items)


def test_doctor_fix_creates_secrets_dir_and_files(tmp_path: Path) -> None:
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


def test_cmd_init_yes_reads_tokens_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--yes writes token env vars to secret files at 0600."""
    root = _make_root(tmp_path)
    monkeypatch.setenv("GITLAB_READ_TOKEN", "glpat-read-from-env")
    monkeypatch.setenv("GITLAB_WRITE_TOKEN", "glpat-write-from-env")
    monkeypatch.setattr("catraz.commands.setup._run_sync", lambda *a, **kw: None)
    monkeypatch.setattr(
        "catraz.commands.setup.run_doctor",
        lambda *a, **kw: types.SimpleNamespace(items=[]),
    )
    monkeypatch.setattr("catraz.commands.setup.print_findings", lambda *a, **kw: (0, 0))

    setup.cmd_init(root, _yes_args(), Out(color=False))

    secrets_dir = root / ".catraz" / "secrets"
    assert (secrets_dir / "gitlab_read_token").read_text() == "glpat-read-from-env"
    assert (secrets_dir / "gitlab_write_token").read_text() == "glpat-write-from-env"
    for filename, _, _ in SECRETS:
        assert stat.S_IMODE((secrets_dir / filename).stat().st_mode) == 0o600


def test_cmd_init_yes_clears_stale_warden_projects_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WARDEN_ALLOWED_PROJECTS is no longer read as input (policy has one
    source, warden.toml, §3.5) — a leftover key from an older catraz version
    is cleared from .env on init so it doesn't linger looking like live config."""
    root = _make_root(tmp_path)
    (root / ".catraz" / ".env").write_text(
        "DEV_UID=1000\nAUTH_MODE=subscription\nWARDEN_ALLOWED_PROJECTS=group/old-proj\n"
    )
    monkeypatch.setattr("catraz.commands.setup._run_sync", lambda *a, **kw: None)
    monkeypatch.setattr(
        "catraz.commands.setup.run_doctor",
        lambda *a, **kw: types.SimpleNamespace(items=[]),
    )
    monkeypatch.setattr("catraz.commands.setup.print_findings", lambda *a, **kw: (0, 0))

    setup.cmd_init(root, _yes_args(), Out(color=False))

    env = load_env(root / ".catraz" / ".env")
    assert "WARDEN_ALLOWED_PROJECTS" not in env


def test_doctor_fix_does_not_overwrite_existing_token(tmp_path: Path) -> None:
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


def test_doctor_fix_secrets_and_claude_are_0700(tmp_path: Path) -> None:
    """secrets/ and secrets/claude/ must both be 0700 after _doctor_fix (C regression guard).

    Ensures the dir-creation order does not cause the umask default (0755) to win
    over the explicit 0700 mode — which would happen if mkdir(parents=True) created
    secrets/ implicitly in the 0755 generic loop before the explicit 0700 call.
    """
    root = _make_root(tmp_path)
    env = load_env(root / ".catraz" / ".env")
    _doctor_fix(root, env)

    secrets_dir = root / ".catraz" / "secrets"
    assert stat.S_IMODE(secrets_dir.stat().st_mode) == 0o700, "secrets/ must be 0700"
    claude_dir = secrets_dir / "claude"
    assert claude_dir.is_dir(), "secrets/claude/ must exist"
    assert stat.S_IMODE(claude_dir.stat().st_mode) == 0o700, "secrets/claude/ must be 0700"
