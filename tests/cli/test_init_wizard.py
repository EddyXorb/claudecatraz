"""Wave-02 — GITLAB_MODE-aware init wizard tests.

Covers:
- --yes mode: GITLAB_MODE inference + policy written to warden.toml
- Interactive mode: choosing off / read-only / read-write; correct secret writes
- TOML setters round-trip (inline comments survive; _read_toml_allowed_projects reads back)
- unset_env_keys unit test
"""

import argparse
import re
import shutil
import stat
import types
from pathlib import Path

import pytest

from catraz.commands import setup
from catraz.envfile import load_env, unset_env_keys
from catraz.policy import (
    _read_toml_allowed_projects,
    remove_toml_key,
    set_toml_list,
    set_toml_scalar,
)
from catraz.ui import Out


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_root(tmp_path: Path) -> Path:
    """Minimal project root with .catraz set up (mirrors test_secrets._make_root)."""
    root = tmp_path / "proj"
    root.mkdir()
    cat = root / ".catraz"
    cat.mkdir()
    (cat / "config").mkdir()
    # Use the real shipped warden.toml so TOML setters are exercised against it.
    from catraz.paths import asset_root

    shipped = asset_root() / "assets" / "config" / "warden.toml"
    dst = cat / "config" / "warden.toml"
    if shipped.exists():
        shutil.copy2(shipped, dst)
    else:
        # Fallback for environments where assets aren't extracted yet
        dst.write_text(
            'branch_prefix       = "claude/"          # R2: comment\n'
            'allowed_projects    = [""]\n'
        )
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


def _interactive_args(force: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        yes=False,
        force=force,
        skip_sync=False,
        dir=None,
        no_color=True,
        print_only=False,
    )


def _patch_common(monkeypatch: pytest.MonkeyPatch) -> None:
    """Suppress sync and doctor so tests focus on the wizard."""
    monkeypatch.setattr("catraz.commands.setup._run_sync", lambda *a, **kw: None)
    monkeypatch.setattr(
        "catraz.commands.setup.run_doctor",
        lambda *a, **kw: types.SimpleNamespace(items=[]),
    )
    monkeypatch.setattr("catraz.commands.setup.print_findings", lambda *a, **kw: (0, 0))


# ---------------------------------------------------------------------------
# --yes mode: GITLAB_MODE inference
# ---------------------------------------------------------------------------


class TestYesGitLabModeInference:
    """_yes_gitlab_mode infers the right mode from env vars."""

    def test_no_tokens_infers_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert setup._yes_gitlab_mode({}) == "off"

    def test_read_only_infers_read_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITLAB_READ_TOKEN", "glpat-read")
        assert setup._yes_gitlab_mode({}) == "read-only"

    def test_both_tokens_infers_read_write(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITLAB_READ_TOKEN", "glpat-read")
        monkeypatch.setenv("GITLAB_WRITE_TOKEN", "glpat-write")
        assert setup._yes_gitlab_mode({}) == "read-write"

    def test_explicit_env_wins_over_inferred(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITLAB_MODE", "off")
        monkeypatch.setenv("GITLAB_READ_TOKEN", "glpat-read")
        monkeypatch.setenv("GITLAB_WRITE_TOKEN", "glpat-write")
        assert setup._yes_gitlab_mode({}) == "off"

    def test_explicit_dotenv_wins_when_no_runtime_env(self) -> None:
        assert setup._yes_gitlab_mode({"GITLAB_MODE": "read-only"}) == "read-only"


class TestYesModeOff:
    """--yes with no tokens → GITLAB_MODE=off; both token files exist (empty)."""

    def test_gitlab_mode_off_written_to_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        setup.cmd_init(root, _yes_args(), Out(color=False))
        env = load_env(root / ".catraz" / ".env")
        assert env.get("GITLAB_MODE") == "off"

    def test_both_token_files_ensured_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        setup.cmd_init(root, _yes_args(), Out(color=False))
        secrets_dir = root / ".catraz" / "secrets"
        for fname in ("gitlab_read_token", "gitlab_write_token"):
            p = secrets_dir / fname
            assert p.exists(), f"missing: {fname}"
            assert stat.S_IMODE(p.stat().st_mode) == 0o600

    def test_no_projects_written_when_off_and_no_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without WARDEN_ALLOWED_PROJECTS env var, warden.toml is not modified."""
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        original = (root / ".catraz" / "config" / "warden.toml").read_text()
        setup.cmd_init(root, _yes_args(), Out(color=False))
        after = (root / ".catraz" / "config" / "warden.toml").read_text()
        assert original == after


class TestYesModeReadOnly:
    """--yes with read token only → GITLAB_MODE=read-only."""

    def test_gitlab_mode_written(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        monkeypatch.setenv("GITLAB_READ_TOKEN", "glpat-read")
        setup.cmd_init(root, _yes_args(), Out(color=False))
        env = load_env(root / ".catraz" / ".env")
        assert env.get("GITLAB_MODE") == "read-only"

    def test_read_token_written(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        monkeypatch.setenv("GITLAB_READ_TOKEN", "glpat-read")
        setup.cmd_init(root, _yes_args(), Out(color=False))
        p = root / ".catraz" / "secrets" / "gitlab_read_token"
        assert p.read_text() == "glpat-read"

    def test_write_token_file_ensured_not_clobbered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Existing write token file must not be overwritten on mode downgrade."""
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        # Pre-populate the write token (simulates downgrade from read-write)
        secrets_dir = root / ".catraz" / "secrets"
        secrets_dir.mkdir(mode=0o700, exist_ok=True)
        (secrets_dir / "gitlab_write_token").write_text("glpat-existing-write")
        (secrets_dir / "gitlab_write_token").chmod(0o600)
        monkeypatch.setenv("GITLAB_READ_TOKEN", "glpat-read")
        setup.cmd_init(root, _yes_args(), Out(color=False))
        # Existing write token must survive
        assert (
            secrets_dir / "gitlab_write_token"
        ).read_text() == "glpat-existing-write"

    def test_warden_projects_from_env_written_to_toml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        monkeypatch.setenv("GITLAB_READ_TOKEN", "glpat-read")
        monkeypatch.setenv("WARDEN_ALLOWED_PROJECTS", "group/proj-a,group/proj-b")
        setup.cmd_init(root, _yes_args(), Out(color=False))
        toml = root / ".catraz" / "config" / "warden.toml"
        assert _read_toml_allowed_projects(toml) == ["group/proj-a", "group/proj-b"]
        # Must NOT appear in .env
        env = load_env(root / ".catraz" / ".env")
        assert "WARDEN_ALLOWED_PROJECTS" not in env

    def test_warden_branch_prefix_csv_written_as_list(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """WARDEN_BRANCH_PREFIX with multiple CSV entries is written as a TOML list."""
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        monkeypatch.setenv("GITLAB_READ_TOKEN", "glpat-read")
        monkeypatch.setenv("WARDEN_BRANCH_PREFIX", "claude/,bot/")
        setup.cmd_init(root, _yes_args(), Out(color=False))
        toml = root / ".catraz" / "config" / "warden.toml"
        text = toml.read_text()
        assert re.search(
            r'branch_prefixes\s*=\s*\[\s*"claude/"\s*,\s*"bot/"\s*\]', text
        )
        assert not re.search(r"^\s*branch_prefix\s*=", text, re.M)


class TestYesModeReadWrite:
    """--yes with both tokens → GITLAB_MODE=read-write."""

    def test_gitlab_mode_written(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        monkeypatch.setenv("GITLAB_READ_TOKEN", "glpat-read")
        monkeypatch.setenv("GITLAB_WRITE_TOKEN", "glpat-write")
        setup.cmd_init(root, _yes_args(), Out(color=False))
        env = load_env(root / ".catraz" / ".env")
        assert env.get("GITLAB_MODE") == "read-write"

    def test_both_tokens_written(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        monkeypatch.setenv("GITLAB_READ_TOKEN", "glpat-read")
        monkeypatch.setenv("GITLAB_WRITE_TOKEN", "glpat-write")
        setup.cmd_init(root, _yes_args(), Out(color=False))
        secrets_dir = root / ".catraz" / "secrets"
        assert (secrets_dir / "gitlab_read_token").read_text() == "glpat-read"
        assert (secrets_dir / "gitlab_write_token").read_text() == "glpat-write"


class TestYesMigration:
    """Stale WARDEN_* keys in .env must be removed after init writes to toml."""

    def test_stale_env_key_removed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        env_path = root / ".catraz" / ".env"
        # Simulate old cmd_init writing WARDEN_ALLOWED_PROJECTS into .env
        env_path.write_text(
            "DEV_UID=1000\nAUTH_MODE=subscription\n"
            "WARDEN_ALLOWED_PROJECTS=group/old-proj\n"
        )
        monkeypatch.setenv("WARDEN_ALLOWED_PROJECTS", "group/new-proj")
        setup.cmd_init(root, _yes_args(), Out(color=False))
        env = load_env(env_path)
        assert "WARDEN_ALLOWED_PROJECTS" not in env

    def test_stale_branch_prefix_removed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        env_path = root / ".catraz" / ".env"
        env_path.write_text(
            "DEV_UID=1000\nAUTH_MODE=subscription\nWARDEN_BRANCH_PREFIX=old/\n"
        )
        monkeypatch.setenv("WARDEN_BRANCH_PREFIX", "new/")
        setup.cmd_init(root, _yes_args(), Out(color=False))
        env = load_env(env_path)
        assert "WARDEN_BRANCH_PREFIX" not in env

    def test_legacy_toml_scalar_migrated_to_list(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pre-existing legacy `branch_prefix = "..."` in warden.toml (from an older
        catraz version) must not survive alongside a freshly written `branch_prefixes`
        list — the Warden's Config aborts if both are set."""
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        toml = root / ".catraz" / "config" / "warden.toml"
        # Simulate an upgrade: strip the shipped list form, add the old scalar form.
        remove_toml_key(toml, "branch_prefixes")
        set_toml_scalar(toml, "branch_prefix", "old/")
        monkeypatch.setenv("GITLAB_READ_TOKEN", "glpat-read")
        monkeypatch.setenv("WARDEN_BRANCH_PREFIX", "new/")
        setup.cmd_init(root, _yes_args(), Out(color=False))
        text = toml.read_text()
        assert re.search(r'branch_prefixes\s*=\s*\[\s*"new/"\s*\]', text)
        assert not re.search(r"^\s*branch_prefix\s*=", text, re.M)


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------


class TestInteractiveModeOff:
    """Choosing 'off' writes GITLAB_MODE=off, never calls getpass, skips policy."""

    def _run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        # choice sequence: gitlab_mode=3 (off)
        # ask sequence: (none needed in off mode)
        inputs = iter(["3"])  # "3" → off (third option)

        def _input(prompt: object) -> str:
            try:
                return next(inputs)
            except StopIteration:
                return ""

        monkeypatch.setattr("builtins.input", _input)

        def _fail_getpass(prompt: object) -> None:
            raise AssertionError("getpass must not be called in off mode")

        monkeypatch.setattr("getpass.getpass", _fail_getpass)
        setup.cmd_init(root, _interactive_args(), Out(color=False))
        return root

    def test_gitlab_mode_off_in_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = self._run(tmp_path, monkeypatch)
        env = load_env(root / ".catraz" / ".env")
        assert env.get("GITLAB_MODE") == "off"

    def test_token_files_exist_without_getpass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = self._run(tmp_path, monkeypatch)
        secrets_dir = root / ".catraz" / "secrets"
        for fname in ("gitlab_read_token", "gitlab_write_token"):
            assert (secrets_dir / fname).exists()

    def test_warden_toml_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        original = (root / ".catraz" / "config" / "warden.toml").read_text()
        monkeypatch.setattr("builtins.input", lambda p: "3")  # always off
        monkeypatch.setattr("getpass.getpass", lambda p: "")
        setup.cmd_init(root, _interactive_args(), Out(color=False))
        after = (root / ".catraz" / "config" / "warden.toml").read_text()
        assert original == after


class TestInteractiveModeReadOnly:
    """Choosing 'read-only' prompts only the read token; write token file ensured."""

    def test_read_only_wizard(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)

        # With force=True the auth_mode choice is always shown (even when AUTH_MODE
        # is already in .env).  Input sequence (one call per out.ask / out.choice):
        #   1) auth_mode choice: "" → default subscription
        #   2) gitlab_mode choice: "2" → read-only
        #   3) out.ask("GitLab base URL") → "" → https://gitlab.com (default)
        #   4) out.ask("projects...") → "group/my-proj"
        #   5) out.ask("Branch prefix") → "" → "claude/" (default)
        inputs = iter(["", "2", "", "group/my-proj", ""])

        def _input(prompt: object) -> str:
            try:
                return next(inputs)
            except StopIteration:
                return ""

        monkeypatch.setattr("builtins.input", _input)

        getpass_calls: list[object] = []

        def _getpass(prompt: object) -> str:
            getpass_calls.append(prompt)
            return "glpat-readtoken"

        monkeypatch.setattr("getpass.getpass", _getpass)
        setup.cmd_init(root, _interactive_args(force=True), Out(color=False))

        env = load_env(root / ".catraz" / ".env")
        assert env.get("GITLAB_MODE") == "read-only"

        secrets_dir = root / ".catraz" / "secrets"
        assert (
            secrets_dir / "gitlab_read_token"
        ).read_text().strip() == "glpat-readtoken"

        # Write token: ensured (empty) but not prompted
        assert (secrets_dir / "gitlab_write_token").exists()
        assert len(getpass_calls) == 1, "only the read token should be prompted"

        # Policy in warden.toml
        toml = root / ".catraz" / "config" / "warden.toml"
        assert _read_toml_allowed_projects(toml) == ["group/my-proj"]

        # branch_prefix default — written in the new list syntax, one entry
        text = toml.read_text()
        assert "branch_prefixes" in text
        assert re.search(r'branch_prefixes\s*=\s*\[\s*"claude/"\s*\]', text)
        # No legacy scalar key left behind alongside the list (Config aborts on both).
        assert not re.search(r"^\s*branch_prefix\s*=", text, re.M)

    def test_write_token_not_clobbered_when_existing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If write token file already has content, read-only mode must not overwrite it."""
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        secrets_dir = root / ".catraz" / "secrets"
        secrets_dir.mkdir(mode=0o700, exist_ok=True)
        (secrets_dir / "gitlab_write_token").write_text("glpat-existing")
        (secrets_dir / "gitlab_write_token").chmod(0o600)

        # force=True → auth_mode prompt shown; sequence:
        #   "" → subscription; "2" → read-only; "" → url; "" → projects; "" → branch
        inputs = iter(["", "2", "", "", ""])

        def _input(p: object) -> str:
            try:
                return next(inputs)
            except StopIteration:
                return ""

        monkeypatch.setattr("builtins.input", _input)
        monkeypatch.setattr("getpass.getpass", lambda p: "glpat-read")
        setup.cmd_init(root, _interactive_args(force=True), Out(color=False))

        assert (secrets_dir / "gitlab_write_token").read_text() == "glpat-existing"


class TestInteractiveModeReadWrite:
    """Choosing 'read-write' prompts both tokens."""

    def test_read_write_wizard(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)

        # "1" → read-write (first option, or just empty = default)
        monkeypatch.setattr("builtins.input", lambda p: "")

        secrets = iter(["glpat-readtoken", "glpat-writetoken"])
        monkeypatch.setattr("getpass.getpass", lambda p: next(secrets))
        setup.cmd_init(root, _interactive_args(force=True), Out(color=False))

        env = load_env(root / ".catraz" / ".env")
        assert env.get("GITLAB_MODE") == "read-write"

        secrets_dir = root / ".catraz" / "secrets"
        assert (
            secrets_dir / "gitlab_read_token"
        ).read_text().strip() == "glpat-readtoken"
        assert (
            secrets_dir / "gitlab_write_token"
        ).read_text().strip() == "glpat-writetoken"

    def test_enter_on_default_selects_read_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty input on the GitLab mode choice selects the default (read-write)."""
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        monkeypatch.setattr("builtins.input", lambda p: "")
        monkeypatch.setattr("getpass.getpass", lambda p: "")
        setup.cmd_init(root, _interactive_args(force=True), Out(color=False))
        env = load_env(root / ".catraz" / ".env")
        assert env.get("GITLAB_MODE") == "read-write"


class TestInteractiveAuthMode:
    """AUTH_MODE is prompted when absent from .env (or --force)."""

    def test_auth_mode_kept_when_present_and_no_force(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When AUTH_MODE=subscription is already in .env and force=False, no prompt."""
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        # Empty input → defaults everywhere; "3" picks off for GitLab
        inputs = iter(["3"])

        def _input(p: object) -> str:
            try:
                return next(inputs)
            except StopIteration:
                return ""

        monkeypatch.setattr("builtins.input", _input)
        monkeypatch.setattr("getpass.getpass", lambda p: "")
        setup.cmd_init(root, _interactive_args(force=False), Out(color=False))
        env = load_env(root / ".catraz" / ".env")
        assert env.get("AUTH_MODE") == "subscription"


# ---------------------------------------------------------------------------
# TOML setters — must round-trip against the real shipped template
# ---------------------------------------------------------------------------


class TestTomlSetters:
    """set_toml_scalar / set_toml_list preserve inline comments and round-trip."""

    def _copy_template(self, tmp_path: Path) -> Path:
        from catraz.paths import asset_root

        shipped = asset_root() / "assets" / "config" / "warden.toml"
        dst = tmp_path / "warden.toml"
        if shipped.exists():
            shutil.copy2(shipped, dst)
        else:
            dst.write_text(
                'branch_prefix       = "claude/"          # R2: only branches\n'
                'allowed_projects    = [""]\n'
            )
        return dst

    def test_set_toml_list_updates_allowed_projects(self, tmp_path: Path) -> None:
        toml = self._copy_template(tmp_path)
        set_toml_list(toml, "allowed_projects", ["group/proj-a", "group/proj-b"])
        result = _read_toml_allowed_projects(toml)
        assert result == ["group/proj-a", "group/proj-b"]

    def test_set_toml_list_preserves_inline_comment(self, tmp_path: Path) -> None:
        """set_toml_list must not strip inline comments from other lines."""
        toml = self._copy_template(tmp_path)
        original_text = toml.read_text()
        set_toml_list(toml, "allowed_projects", ["group/proj"])
        new_text = toml.read_text()
        # The branch_prefix line's comment must survive
        if "# R2:" in original_text:
            assert "# R2:" in new_text

    def test_set_toml_scalar_updates_branch_prefix(self, tmp_path: Path) -> None:
        toml = self._copy_template(tmp_path)
        set_toml_scalar(toml, "branch_prefix", "feat/")
        text = toml.read_text()
        assert '"feat/"' in text

    def test_set_toml_scalar_preserves_comment(self, tmp_path: Path) -> None:
        """set_toml_scalar must preserve the inline comment on the branch_prefix line."""
        toml = self._copy_template(tmp_path)
        original_text = toml.read_text()
        set_toml_scalar(toml, "branch_prefix", "feat/")
        new_text = toml.read_text()
        # Comment text after branch_prefix line must survive
        if "# R2:" in original_text:
            assert "# R2:" in new_text

    def test_set_toml_list_appends_when_key_absent(self, tmp_path: Path) -> None:
        toml = tmp_path / "min.toml"
        toml.write_text("max_open_mrs = 5\n")
        set_toml_list(toml, "allowed_projects", ["group/proj"])
        assert _read_toml_allowed_projects(toml) == ["group/proj"]

    def test_set_toml_scalar_appends_when_key_absent(self, tmp_path: Path) -> None:
        toml = tmp_path / "min.toml"
        toml.write_text("max_open_mrs = 5\n")
        set_toml_scalar(toml, "branch_prefix", "ci/")
        assert '"ci/"' in toml.read_text()

    def test_set_toml_list_matches_shipped_format(self, tmp_path: Path) -> None:
        """The shipped allowed_projects = [""] (one empty string) must be matched."""
        toml = self._copy_template(tmp_path)
        # The template has [""] — set_toml_list must replace it
        set_toml_list(toml, "allowed_projects", ["mygroup/myproject"])
        assert _read_toml_allowed_projects(toml) == ["mygroup/myproject"]

    def test_set_toml_list_roundtrip_readable_by_function(self, tmp_path: Path) -> None:
        """Values written by set_toml_list must be readable by _read_toml_allowed_projects."""
        toml = self._copy_template(tmp_path)
        projects = ["a/b", "c/d/e", "x/y"]
        set_toml_list(toml, "allowed_projects", projects)
        assert _read_toml_allowed_projects(toml) == projects


# ---------------------------------------------------------------------------
# unset_env_keys
# ---------------------------------------------------------------------------


class TestUnsetEnvKeys:
    def test_removes_active_key(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "DEV_UID=1000\nWARDEN_ALLOWED_PROJECTS=group/proj\nAUTH_MODE=sub\n"
        )
        unset_env_keys(env_file, ["WARDEN_ALLOWED_PROJECTS"])
        env = load_env(env_file)
        assert "WARDEN_ALLOWED_PROJECTS" not in env
        assert env.get("DEV_UID") == "1000"

    def test_leaves_comments_untouched(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "# WARDEN_ALLOWED_PROJECTS=\nWARDEN_ALLOWED_PROJECTS=group/proj\n"
        )
        unset_env_keys(env_file, ["WARDEN_ALLOWED_PROJECTS"])
        text = env_file.read_text()
        assert "# WARDEN_ALLOWED_PROJECTS=" in text
        env = load_env(env_file)
        assert "WARDEN_ALLOWED_PROJECTS" not in env

    def test_noop_when_key_absent(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("DEV_UID=1000\n")
        unset_env_keys(env_file, ["WARDEN_ALLOWED_PROJECTS"])
        assert load_env(env_file).get("DEV_UID") == "1000"

    def test_noop_when_file_missing(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.env"
        unset_env_keys(missing, ["FOO"])  # must not raise

    def test_removes_multiple_keys(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "A=1\nWARDEN_ALLOWED_PROJECTS=g/p\nWARDEN_BRANCH_PREFIX=claude/\nB=2\n"
        )
        unset_env_keys(env_file, ["WARDEN_ALLOWED_PROJECTS", "WARDEN_BRANCH_PREFIX"])
        env = load_env(env_file)
        assert "WARDEN_ALLOWED_PROJECTS" not in env
        assert "WARDEN_BRANCH_PREFIX" not in env
        assert env.get("A") == "1"
        assert env.get("B") == "2"


# ---------------------------------------------------------------------------
# Base image wizard
# ---------------------------------------------------------------------------


class TestBaseImageWizard:
    """Tests for base image configuration in both interactive and --yes modes.

    Interactive base-image prompt removed (Workstream A): the base Dockerfile is
    now seeded to .catraz/config/image/Dockerfile by cmd_init. BASE_* remain as
    .env power-user overrides handled by _wizard_yes.
    """

    def _run_interactive(
        self,
        root: Path,
        monkeypatch: pytest.MonkeyPatch,
        inputs: list[str],
        force: bool = False,
    ) -> dict[str, str]:
        """Helper: run cmd_init in interactive mode with the given input sequence."""
        _patch_common(monkeypatch)
        it = iter(inputs)

        def _input(prompt: object) -> str:
            try:
                return next(it)
            except StopIteration:
                return ""

        monkeypatch.setattr("builtins.input", _input)
        monkeypatch.setattr("getpass.getpass", lambda p: "")
        setup.cmd_init(root, _interactive_args(force=force), Out(color=False))
        return load_env(root / ".catraz" / ".env")

    # ------------------------------------------------------------------
    # Interactive: no base-image prompt; Dockerfile seeded to config/image/
    # ------------------------------------------------------------------

    def test_no_base_image_prompt_in_interactive(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Interactive wizard no longer prompts for base image choice."""
        root = _make_root(tmp_path)
        # "3" → gitlab off; no further inputs needed (prompt removed)
        env = self._run_interactive(root, monkeypatch, ["3"])
        assert "BASE_IMAGE" not in env
        assert "BASE_DOCKERFILE" not in env

    def test_local_dockerfile_seeded_by_init(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cmd_init seeds .catraz/config/image/Dockerfile (FROM ubuntu:24.04)."""
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        monkeypatch.setattr("builtins.input", lambda p: "3")  # gitlab off
        monkeypatch.setattr("getpass.getpass", lambda p: "")
        setup.cmd_init(root, _interactive_args(), Out(color=False))
        df = root / ".catraz" / "config" / "image" / "Dockerfile"
        assert df.exists(), "config/image/Dockerfile must be seeded by init"
        assert "ubuntu:24.04" in df.read_text()

    def test_existing_base_image_env_preserved_on_interactive(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """BASE_IMAGE already in .env must be preserved (wizard doesn't touch it)."""
        root = _make_root(tmp_path)
        env_path = root / ".catraz" / ".env"
        env_path.write_text(
            "DEV_UID=1000\nAUTH_MODE=subscription\nBASE_IMAGE=python:3.11\n"
        )
        env = self._run_interactive(root, monkeypatch, ["3"], force=False)
        assert env.get("BASE_IMAGE") == "python:3.11"

    # ------------------------------------------------------------------
    # --yes mode tests (BASE_* env override still supported)
    # ------------------------------------------------------------------

    def test_yes_base_image_from_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--yes with BASE_IMAGE env var writes BASE_IMAGE to .env."""
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        monkeypatch.setenv("BASE_IMAGE", "python:3.11")
        setup.cmd_init(root, _yes_args(), Out(color=False))
        env = load_env(root / ".catraz" / ".env")
        assert env.get("BASE_IMAGE") == "python:3.11"

    def test_yes_base_dockerfile_from_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--yes with BASE_DOCKERFILE env var writes BASE_DOCKERFILE to .env."""
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        monkeypatch.setenv("BASE_DOCKERFILE", "./Dockerfile")
        setup.cmd_init(root, _yes_args(), Out(color=False))
        env = load_env(root / ".catraz" / ".env")
        assert env.get("BASE_DOCKERFILE") == "./Dockerfile"

    def test_yes_base_image_takes_priority_over_dockerfile(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--yes with both BASE_IMAGE and BASE_DOCKERFILE: BASE_IMAGE wins, BASE_DOCKERFILE removed."""
        root = _make_root(tmp_path)
        env_path = root / ".catraz" / ".env"
        # Pre-populate BASE_DOCKERFILE in .env to test removal
        env_path.write_text(
            "DEV_UID=1000\nAUTH_MODE=subscription\nBASE_DOCKERFILE=./Dockerfile\n"
        )
        _patch_common(monkeypatch)
        monkeypatch.setenv("BASE_IMAGE", "img:1")
        monkeypatch.setenv("BASE_DOCKERFILE", "./Dockerfile")
        setup.cmd_init(root, _yes_args(), Out(color=False))
        env = load_env(env_path)
        assert env.get("BASE_IMAGE") == "img:1"
        assert "BASE_DOCKERFILE" not in env
