"""Workstream D — catraz init --from <path> tests."""
import argparse
import types
from pathlib import Path

import pytest

from catraz.commands import setup
from catraz.commands.setup._from import load_inherited, stage_inherited, _ENV_ALLOWLIST
from catraz.envfile import load_env
from catraz.ui import Out


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_source(tmp_path: Path, *, auth_mode: str = "subscription",
                 gitlab_mode: str = "read-write",
                 gitlab_url: str = "https://gitlab.example.com",
                 dev_uid: str = "9999") -> Path:
    """Create a fully initialised source sandbox."""
    src = tmp_path / "src"
    src.mkdir()
    cat = src / ".catraz"
    cat.mkdir()
    (cat / "config").mkdir()
    (cat / "config" / "image").mkdir(parents=True)
    (cat / "config" / "image" / "Dockerfile").write_text("FROM ubuntu:24.04\nRUN echo src\n")
    (cat / "config" / "warden.toml").write_text('allowed_projects = ["group/proj"]\n')
    (cat / "config" / "squid.conf").write_text("# squid src\n")
    (cat / "config" / "allowlist.txt").write_text("example.com\n")
    (cat / ".env").write_text(
        f"AUTH_MODE={auth_mode}\n"
        f"GITLAB_MODE={gitlab_mode}\n"
        f"GITLAB_URL={gitlab_url}\n"
        f"DEV_UID={dev_uid}\n"
    )
    secrets = cat / "secrets"
    secrets.mkdir(mode=0o700)
    (secrets / "gitlab_read_token").write_text("glpat-src-read")
    (secrets / "gitlab_read_token").chmod(0o600)
    (secrets / "gitlab_write_token").write_text("glpat-src-write")
    (secrets / "gitlab_write_token").chmod(0o600)
    claude_dir = secrets / "claude"
    claude_dir.mkdir(mode=0o700)
    (claude_dir / ".credentials.json").write_text('{"token":"src-cred"}')
    (claude_dir / ".credentials.json").chmod(0o600)
    return src


def _make_dst(tmp_path: Path) -> Path:
    dst = tmp_path / "dst"
    dst.mkdir()
    cat = dst / ".catraz"
    cat.mkdir()
    (cat / "config").mkdir()
    (cat / "config" / "warden.toml").write_text('allowed_projects = [""]\n')
    (cat / ".env").write_text("DEV_UID=1000\nAUTH_MODE=subscription\n")
    return dst


def _yes_args(init_from: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(
        yes=True, force=False, skip_sync=True,
        dir=None, no_color=True, print_only=False,
        init_from=init_from,
    )


def _patch_common(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("catraz.commands.setup._run_sync", lambda *a, **kw: None)
    monkeypatch.setattr("catraz.commands.setup.run_doctor",
                        lambda *a, **kw: types.SimpleNamespace(items=[]))
    monkeypatch.setattr("catraz.commands.setup.print_findings",
                        lambda *a, **kw: (0, 0))


# ── load_inherited unit tests ─────────────────────────────────────────────────

def test_load_inherited_invalid_path(tmp_path: Path) -> None:
    """Missing .catraz raises CliError."""
    from catraz.errors import CliError
    with pytest.raises(CliError):
        load_inherited(tmp_path / "nonexistent")


def test_load_inherited_no_catraz_dir(tmp_path: Path) -> None:
    """A dir without .catraz/ raises CliError."""
    from catraz.errors import CliError
    with pytest.raises(CliError):
        load_inherited(tmp_path)


def test_load_inherited_curated_env_keys(tmp_path: Path) -> None:
    """Only allowlisted keys are returned; DEV_UID is excluded."""
    src = _make_source(tmp_path)
    result = load_inherited(src)
    env = result["env"]
    assert "DEV_UID" not in env, "DEV_UID must not be inherited"
    for k in ("AUTH_MODE", "GITLAB_MODE", "GITLAB_URL"):
        assert k in env, f"{k} should be in inherited env"


def test_load_inherited_all_allowlist_keys_only(tmp_path: Path) -> None:
    """No key outside _ENV_ALLOWLIST appears in inherited env."""
    src = _make_source(tmp_path)
    result = load_inherited(src)
    for k in result["env"]:
        assert k in _ENV_ALLOWLIST, f"unexpected key {k!r} in inherited env"


def test_load_inherited_config_files(tmp_path: Path) -> None:
    """Existing config files are discovered."""
    src = _make_source(tmp_path)
    result = load_inherited(src)
    assert "image/Dockerfile" in result["config"]
    assert "warden.toml" in result["config"]


def test_load_inherited_secrets(tmp_path: Path) -> None:
    """Secrets directory children are included."""
    src = _make_source(tmp_path)
    result = load_inherited(src)
    assert "gitlab_read_token" in result["secrets"]
    assert "claude" in result["secrets"]


def test_load_inherited_skips_empty_secrets(tmp_path: Path) -> None:
    """Empty or whitespace-only secret files are not inherited."""
    src = tmp_path / "src"
    src.mkdir()
    cat = src / ".catraz"
    cat.mkdir()
    (cat / ".env").write_text("AUTH_MODE=subscription\n")
    secrets = cat / "secrets"
    secrets.mkdir(mode=0o700)
    (secrets / "gitlab_read_token").write_text("")
    (secrets / "gitlab_read_token").chmod(0o600)
    (secrets / "gitlab_write_token").write_text("  \n")
    (secrets / "gitlab_write_token").chmod(0o600)
    result = load_inherited(src)
    assert "gitlab_read_token" not in result["secrets"]
    assert "gitlab_write_token" not in result["secrets"]


def test_stage_inherited_overwrites_empty_destination(tmp_path: Path) -> None:
    """stage_inherited copies source secrets even when the destination file already
    exists but is empty — e.g. a re-init after a partial setup."""
    src = _make_source(tmp_path)
    dst = tmp_path / "dst"
    dst.mkdir()
    cat = dst / ".catraz"
    cat.mkdir()
    secrets = cat / "secrets"
    secrets.mkdir(mode=0o700)
    # Pre-seed empty placeholder files (like _ensure_secret creates).
    (secrets / "gitlab_read_token").write_text("")
    (secrets / "gitlab_read_token").chmod(0o600)
    (secrets / "gitlab_write_token").write_text("")
    (secrets / "gitlab_write_token").chmod(0o600)

    inherited = load_inherited(src)
    stage_inherited(cat, inherited, yes=False, out=Out(color=False))

    assert (secrets / "gitlab_read_token").read_text() == "glpat-src-read"
    assert (secrets / "gitlab_write_token").read_text() == "glpat-src-write"


# ── -y (non-interactive) clone ────────────────────────────────────────────────

def test_yes_clone_inherits_env_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """-y --from clones curated .env keys to the destination."""
    src = _make_source(tmp_path)
    dst = _make_dst(tmp_path)
    _patch_common(monkeypatch)
    setup.cmd_init(dst, _yes_args(str(src)), Out(color=False))
    env = load_env(dst / ".catraz" / ".env")
    assert env.get("AUTH_MODE") == "subscription"
    assert env.get("GITLAB_MODE") == "read-write"
    assert env.get("GITLAB_URL") == "https://gitlab.example.com"


def test_yes_clone_does_not_inherit_dev_uid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """-y --from must NOT inherit DEV_UID; it is set locally."""
    src = _make_source(tmp_path, dev_uid="9999")
    dst = _make_dst(tmp_path)
    _patch_common(monkeypatch)
    setup.cmd_init(dst, _yes_args(str(src)), Out(color=False))
    env = load_env(dst / ".catraz" / ".env")
    assert env.get("DEV_UID") != "9999", "DEV_UID must not be inherited from source"


def test_yes_clone_copies_secrets_without_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """-y --from copies secrets; env vars override."""
    src = _make_source(tmp_path)
    dst = _make_dst(tmp_path)
    _patch_common(monkeypatch)
    setup.cmd_init(dst, _yes_args(str(src)), Out(color=False))
    secrets_dir = dst / ".catraz" / "secrets"
    # Inherited token must be present.
    tok = secrets_dir / "gitlab_read_token"
    assert tok.exists()
    # Env override takes precedence.
    monkeypatch.setenv("GITLAB_READ_TOKEN", "glpat-env-override")
    setup.cmd_init(dst, _yes_args(str(src)), Out(color=False))
    # After env-override run, the env var was applied via _yes_apply_tokens.
    assert (secrets_dir / "gitlab_read_token").read_text() == "glpat-env-override"


def test_yes_clone_copies_claude_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """-y --from copies secrets/claude/ without printing its contents."""
    src = _make_source(tmp_path)
    dst = _make_dst(tmp_path)
    _patch_common(monkeypatch)
    setup.cmd_init(dst, _yes_args(str(src)), Out(color=False))
    cred = dst / ".catraz" / "secrets" / "claude" / ".credentials.json"
    assert cred.exists(), "secrets/claude/.credentials.json must be inherited"
    # The content must be the source content (not echoed by the wizard).
    assert "src-cred" in cred.read_text()


def test_yes_clone_config_file_copied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """-y --from copies config/image/Dockerfile from source."""
    src = _make_source(tmp_path)
    dst = _make_dst(tmp_path)
    _patch_common(monkeypatch)
    setup.cmd_init(dst, _yes_args(str(src)), Out(color=False))
    df = dst / ".catraz" / "config" / "image" / "Dockerfile"
    assert df.exists()
    assert "echo src" in df.read_text()


# ── interactive (no -y) clone — ordering regression ───────────────────────────

def _interactive_args(init_from: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(
        yes=False, force=False, skip_sync=True,
        dir=None, no_color=True, print_only=False,
        init_from=init_from,
    )


def test_interactive_clone_inherits_config_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Interactive `init --from` must inherit config/ files (Dockerfile, allowlist,
    squid.conf), not silently keep the freshly-seeded defaults.

    Regression: _init_config_templates seeded the defaults first, so stage_inherited's
    `not dst.exists()` guard skipped the inherited copies in interactive mode (where its
    `yes` override is off). The fix stages inherited files before seeding defaults.
    """
    src = _make_source(tmp_path, gitlab_mode="off")  # off → wizard needs no tokens
    dst = _make_dst(tmp_path)
    _patch_common(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda p: "")     # accept inherited defaults
    monkeypatch.setattr("getpass.getpass", lambda p: "")
    setup.cmd_init(dst, _interactive_args(str(src)), Out(color=False))

    cfg = dst / ".catraz" / "config"
    assert "echo src" in (cfg / "image" / "Dockerfile").read_text(), "Dockerfile not inherited"
    assert (cfg / "allowlist.txt").read_text() == "example.com\n", "allowlist not inherited"
    assert (cfg / "squid.conf").read_text() == "# squid src\n", "squid.conf not inherited"


# ── secret never printed ──────────────────────────────────────────────────────

def test_secret_never_echoed_to_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Inherited token value must never appear in stdout or stderr."""
    src = _make_source(tmp_path)
    dst = _make_dst(tmp_path)
    _patch_common(monkeypatch)
    setup.cmd_init(dst, _yes_args(str(src)), Out(color=False))
    captured = capsys.readouterr()
    assert "glpat-src-read" not in captured.out
    assert "glpat-src-read" not in captured.err
    assert "glpat-src-write" not in captured.out
    assert "glpat-src-write" not in captured.err
    assert "src-cred" not in captured.out
    assert "src-cred" not in captured.err


# ── error on invalid path ─────────────────────────────────────────────────────

def test_invalid_from_path_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """--from pointing to a non-initialised dir raises CliError."""
    from catraz.errors import CliError
    dst = _make_dst(tmp_path)
    _patch_common(monkeypatch)
    with pytest.raises(CliError):
        setup.cmd_init(dst, _yes_args(str(tmp_path / "does_not_exist")), Out(color=False))
