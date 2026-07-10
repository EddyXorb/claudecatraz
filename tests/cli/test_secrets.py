"""Init writes host-keyed grouped tokens into .catraz/secrets/."""

import argparse
import stat
import types
from pathlib import Path

import pytest

from catraz.commands import setup
from catraz.commands.setup._secrets import _read_grouped_token
from catraz.doctor import run_doctor, _doctor_fix
from catraz.envfile import load_env
from catraz.ui import Out

_GROUPED = ("read_tokens", "write_tokens")


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


def _patch_common(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("catraz.commands.setup._run_sync", lambda *a, **kw: None)
    monkeypatch.setattr(
        "catraz.commands.setup.run_doctor",
        lambda *a, **kw: types.SimpleNamespace(items=[]),
    )
    monkeypatch.setattr("catraz.commands.setup.print_findings", lambda *a, **kw: (0, 0))


def test_cmd_init_creates_grouped_token_files_even_blank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cmd_init --yes creates secrets/ at 0700 and both grouped token files at
    0600, even with no token env vars; no GITLAB_MODE lands in .env."""
    root = _make_root(tmp_path)
    _patch_common(monkeypatch)

    setup.cmd_init(root, _yes_args(), Out(color=False))

    secrets_dir = root / ".catraz" / "secrets"
    assert secrets_dir.is_dir()
    assert stat.S_IMODE(secrets_dir.stat().st_mode) == 0o700
    for filename in _GROUPED:
        p = secrets_dir / filename
        assert p.exists(), f"missing: {p}"
        assert stat.S_IMODE(p.stat().st_mode) == 0o600

    env = load_env(root / ".catraz" / ".env")
    assert "GITLAB_MODE" not in env
    assert "GITLAB_URL" not in env


def test_cmd_init_writes_host_keyed_token_via_getpass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Interactive init upserts `<host> <token>` lines into the grouped files."""
    root = _make_root(tmp_path)

    secrets = iter(["glpat-readtoken", "glpat-writetoken"])
    monkeypatch.setattr("getpass.getpass", lambda prompt: next(secrets))
    monkeypatch.setattr("builtins.input", lambda prompt: "")  # defaults everywhere
    _patch_common(monkeypatch)

    args = argparse.Namespace(
        yes=False,
        force=False,
        skip_sync=False,
        dir=None,
        no_color=True,
        print_only=False,
    )
    setup.cmd_init(root, args, Out(color=False))

    secrets_dir = root / ".catraz" / "secrets"
    assert (secrets_dir / "read_tokens").read_text() == "gitlab.com glpat-readtoken\n"
    assert (secrets_dir / "write_tokens").read_text() == "gitlab.com glpat-writetoken\n"
    for filename in _GROUPED:
        assert stat.S_IMODE((secrets_dir / filename).stat().st_mode) == 0o600


def test_cmd_init_writes_git_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The wizard ensures a [[git.endpoint]] (host, type=gitlab) in warden.toml."""
    import tomllib

    root = _make_root(tmp_path)
    _patch_common(monkeypatch)
    setup.cmd_init(root, _yes_args(), Out(color=False))
    data = tomllib.loads((root / ".catraz" / "config" / "warden.toml").read_text())
    endpoints = data["git"]["endpoint"]
    assert {(e["host"], e["type"]) for e in endpoints} == {("gitlab.com", "gitlab")}


def test_doctor_fix_on_fresh_root_creates_catraz(tmp_path: Path) -> None:
    """_doctor_fix on a project where .catraz/ does not exist yet must not crash."""
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
    """An `[[git.endpoint]]` with no token in the grouped files warns "closed" —
    never a failing (bad) exit; the Warden, not doctor, enforces fail-closed."""
    root = _make_root(tmp_path)
    (root / ".catraz" / "config" / "warden.toml").write_text(
        '[[git.endpoint]]\nhost = "gitlab.com"\ntype = "gitlab"\n'
    )
    secrets_dir = root / ".catraz" / "secrets"
    secrets_dir.mkdir(mode=0o700)
    for filename in _GROUPED:
        p = secrets_dir / filename
        p.write_text("")
        p.chmod(0o600)

    f = run_doctor(root, only=["tokens"])
    assert not any(i[0] == "bad" for i in f.items)
    assert any(i[0] == "warn" and "gitlab.com" in i[2] and "closed" in i[2] for i in f.items)


def test_doctor_ok_on_non_empty_grouped_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """doctor reports ok when a configured endpoint has both tokens set."""
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


def test_doctor_fix_creates_grouped_files(tmp_path: Path) -> None:
    """doctor --fix always creates secrets/ (0700) and empty grouped files (0600)."""
    root = _make_root(tmp_path)
    env = load_env(root / ".catraz" / ".env")
    _doctor_fix(root, env)

    secrets_dir = root / ".catraz" / "secrets"
    assert secrets_dir.is_dir()
    assert stat.S_IMODE(secrets_dir.stat().st_mode) == 0o700
    for filename in _GROUPED:
        p = secrets_dir / filename
        assert p.exists(), f"missing: {p}"
        assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_cmd_init_yes_reads_tokens_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--yes upserts token env vars into the grouped files, keyed by host."""
    root = _make_root(tmp_path)
    monkeypatch.setenv("GITLAB_READ_TOKEN", "glpat-read-from-env")
    monkeypatch.setenv("GITLAB_WRITE_TOKEN", "glpat-write-from-env")
    _patch_common(monkeypatch)

    setup.cmd_init(root, _yes_args(), Out(color=False))

    secrets_dir = root / ".catraz" / "secrets"
    assert _read_grouped_token(secrets_dir, "read_tokens", "gitlab.com") == "glpat-read-from-env"
    assert _read_grouped_token(secrets_dir, "write_tokens", "gitlab.com") == "glpat-write-from-env"
    for filename in _GROUPED:
        assert stat.S_IMODE((secrets_dir / filename).stat().st_mode) == 0o600


def test_cmd_init_yes_reads_host_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--yes honours GITLAB_HOST for both the token key and the endpoint."""
    import tomllib

    root = _make_root(tmp_path)
    monkeypatch.setenv("GITLAB_HOST", "gitlab.example.com")
    monkeypatch.setenv("GITLAB_READ_TOKEN", "glpat-self-hosted")
    _patch_common(monkeypatch)

    setup.cmd_init(root, _yes_args(), Out(color=False))

    secrets_dir = root / ".catraz" / "secrets"
    assert (
        _read_grouped_token(secrets_dir, "read_tokens", "gitlab.example.com")
        == "glpat-self-hosted"
    )
    data = tomllib.loads((root / ".catraz" / "config" / "warden.toml").read_text())
    assert {e["host"] for e in data["git"]["endpoint"]} == {"gitlab.example.com"}


def test_cmd_init_yes_clears_stale_warden_projects_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_root(tmp_path)
    (root / ".catraz" / ".env").write_text(
        "DEV_UID=1000\nAUTH_MODE=subscription\nWARDEN_ALLOWED_PROJECTS=group/old-proj\n"
    )
    _patch_common(monkeypatch)

    setup.cmd_init(root, _yes_args(), Out(color=False))

    env = load_env(root / ".catraz" / ".env")
    assert "WARDEN_ALLOWED_PROJECTS" not in env


def test_doctor_fix_does_not_overwrite_existing_token(tmp_path: Path) -> None:
    """doctor --fix leaves an already-populated grouped file unchanged."""
    root = _make_root(tmp_path)
    secrets_dir = root / ".catraz" / "secrets"
    secrets_dir.mkdir(mode=0o700)
    (secrets_dir / "read_tokens").write_text("gitlab.com glpat-existing\n")
    (secrets_dir / "read_tokens").chmod(0o600)

    env = load_env(root / ".catraz" / ".env")
    _doctor_fix(root, env)

    assert (secrets_dir / "read_tokens").read_text() == "gitlab.com glpat-existing\n"


def test_doctor_fix_secrets_and_claude_are_0700(tmp_path: Path) -> None:
    """secrets/ and secrets/claude/ must both be 0700 after _doctor_fix."""
    root = _make_root(tmp_path)
    env = load_env(root / ".catraz" / ".env")
    _doctor_fix(root, env)

    secrets_dir = root / ".catraz" / "secrets"
    assert stat.S_IMODE(secrets_dir.stat().st_mode) == 0o700, "secrets/ must be 0700"
    claude_dir = secrets_dir / "claude"
    assert claude_dir.is_dir(), "secrets/claude/ must exist"
    assert stat.S_IMODE(claude_dir.stat().st_mode) == 0o700, "secrets/claude/ must be 0700"
