"""Init wizard tests: one host prompt (default gitlab.com), grouped host-keyed
tokens, an auto-synthesised [[git.endpoint]], top-level policy writes, TOML
setter round-trips, and unset_env_keys. Access mode is the presence of a write
token, never a stored GITLAB_MODE."""

import argparse
import re
import shutil
import stat
import tomllib
import types
from pathlib import Path

import pytest

from catraz.commands import setup
from catraz.commands.setup._secrets import _read_grouped_token
from catraz.envfile import load_env, unset_env_keys
from catraz.policy import (
    _read_toml_allowed_projects,
    set_toml_list,
    set_toml_scalar,
)
from catraz.ui import Out


# Fixtures / helpers


def _make_root(tmp_path: Path) -> Path:
    """Minimal project root with .catraz set up (mirrors test_secrets._make_root)."""
    root = tmp_path / "proj"
    root.mkdir()
    cat = root / ".catraz"
    cat.mkdir()
    (cat / "config").mkdir()
    from catraz.paths import asset_root

    shipped = asset_root() / "assets" / "config" / "warden.toml"
    dst = cat / "config" / "warden.toml"
    if shipped.exists():
        shutil.copy2(shipped, dst)
    else:
        dst.write_text(
            'branch_prefix       = "claude/"          # R2: comment\nallowed_projects    = [""]\n'
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


def _endpoints(root: Path) -> set[tuple[str, str]]:
    data = tomllib.loads((root / ".catraz" / "config" / "warden.toml").read_text())
    return {(e["host"], e["type"]) for e in data.get("git", {}).get("endpoint", [])}


# --yes: env-driven tokens + endpoint


class TestYesNoTokens:
    """--yes with no token env vars: grouped files stay empty, no GITLAB_MODE in
    .env, but the single-host endpoint is still created (it runs closed)."""

    def test_no_gitlab_mode_in_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        setup.cmd_init(root, _yes_args(), Out(color=False))
        env = load_env(root / ".catraz" / ".env")
        assert "GITLAB_MODE" not in env
        assert "GITLAB_URL" not in env

    def test_grouped_files_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        setup.cmd_init(root, _yes_args(), Out(color=False))
        secrets_dir = root / ".catraz" / "secrets"
        for fname in ("read_tokens", "write_tokens"):
            p = secrets_dir / fname
            assert p.exists()
            assert p.read_text() == ""
            assert stat.S_IMODE(p.stat().st_mode) == 0o600

    def test_endpoint_created(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        setup.cmd_init(root, _yes_args(), Out(color=False))
        assert _endpoints(root) == {("gitlab.com", "gitlab")}


class TestYesReadOnly:
    """--yes with a read token only → read_tokens set, write_tokens empty."""

    def test_read_token_written(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        monkeypatch.setenv("GITLAB_READ_TOKEN", "glpat-read")
        setup.cmd_init(root, _yes_args(), Out(color=False))
        secrets_dir = root / ".catraz" / "secrets"
        assert _read_grouped_token(secrets_dir, "read_tokens", "gitlab.com") == "glpat-read"
        assert (secrets_dir / "write_tokens").read_text() == ""

    def test_existing_write_token_not_clobbered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        secrets_dir = root / ".catraz" / "secrets"
        secrets_dir.mkdir(mode=0o700, exist_ok=True)
        (secrets_dir / "write_tokens").write_text("gitlab.com glpat-existing-write\n")
        (secrets_dir / "write_tokens").chmod(0o600)
        monkeypatch.setenv("GITLAB_READ_TOKEN", "glpat-read")
        setup.cmd_init(root, _yes_args(), Out(color=False))
        assert (secrets_dir / "write_tokens").read_text() == "gitlab.com glpat-existing-write\n"


class TestYesReadWrite:
    """--yes with both tokens → both grouped lines set under the host."""

    def test_both_tokens_written(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        monkeypatch.setenv("GITLAB_READ_TOKEN", "glpat-read")
        monkeypatch.setenv("GITLAB_WRITE_TOKEN", "glpat-write")
        setup.cmd_init(root, _yes_args(), Out(color=False))
        secrets_dir = root / ".catraz" / "secrets"
        assert _read_grouped_token(secrets_dir, "read_tokens", "gitlab.com") == "glpat-read"
        assert _read_grouped_token(secrets_dir, "write_tokens", "gitlab.com") == "glpat-write"


class TestYesScaffold:
    """`init` leaves a parsable warden.toml carrying the taxonomy plus the
    synthesised endpoint, and empty grouped files it never clobbers."""

    def test_existing_read_write_tokens_not_clobbered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_root(tmp_path)
        secrets_dir = root / ".catraz" / "secrets"
        secrets_dir.mkdir(mode=0o700, exist_ok=True)
        (secrets_dir / "read_tokens").write_text("gitlab.com glpat-existing\n")
        (secrets_dir / "read_tokens").chmod(0o600)
        _patch_common(monkeypatch)
        setup.cmd_init(root, _yes_args(), Out(color=False))
        assert (secrets_dir / "read_tokens").read_text() == "gitlab.com glpat-existing\n"

    def test_warden_toml_parsable_with_taxonomy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        setup.cmd_init(root, _yes_args(), Out(color=False))
        data = tomllib.loads(
            (root / ".catraz" / "config" / "warden.toml").read_text(encoding="utf-8")
        )
        assert "rules" in data.get("git", {})

    def test_warden_toml_scaffolds_explicit_git_actions_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`init` keeps the shipped `[git] actions = [...]` full default plus the
        vocabulary comment, and adds no `[api.endpoints]` remnant."""
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        setup.cmd_init(root, _yes_args(), Out(color=False))
        text = (root / ".catraz" / "config" / "warden.toml").read_text(encoding="utf-8")
        data = tomllib.loads(text)
        assert data["git"]["actions"] == [
            "repo.read",
            "repo.branch.create",
            "repo.branch.push",
            "project.read",
            "project.mr.create",
            "project.mr.edit",
            "project.mr.close",
            "project.mr.comment",
            "project.ci.trigger",
            "instance.projects.read",
            "instance.users.read",
            "instance.meta.read",
        ]
        for action in (
            "repo.branch.delete",
            "repo.tag.create",
            "project.mr.merge",
            "project.issue.create",
            "instance.meta.read",
        ):
            assert action in text
        assert "[api.endpoints]" not in text

    def test_reinit_does_not_duplicate_endpoint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        setup.cmd_init(root, _yes_args(), Out(color=False))
        setup.cmd_init(root, _yes_args(), Out(color=False))
        data = tomllib.loads((root / ".catraz" / "config" / "warden.toml").read_text())
        assert len(data["git"]["endpoint"]) == 1


class TestYesMigration:
    """Stale WARDEN_* keys in .env are removed on init; policy comes from
    warden.toml only."""

    def test_stale_projects_key_removed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        env_path = root / ".catraz" / ".env"
        env_path.write_text(
            "DEV_UID=1000\nAUTH_MODE=subscription\nWARDEN_ALLOWED_PROJECTS=group/old-proj\n"
        )
        setup.cmd_init(root, _yes_args(), Out(color=False))
        assert "WARDEN_ALLOWED_PROJECTS" not in load_env(env_path)

    def test_stale_branch_prefix_removed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        env_path = root / ".catraz" / ".env"
        env_path.write_text("DEV_UID=1000\nAUTH_MODE=subscription\nWARDEN_BRANCH_PREFIX=old/\n")
        setup.cmd_init(root, _yes_args(), Out(color=False))
        assert "WARDEN_BRANCH_PREFIX" not in load_env(env_path)


# Interactive mode


class TestInteractiveReadWrite:
    """Default GitLab access (read-write) prompts both tokens and writes both
    grouped lines, the endpoint, and top-level policy."""

    def test_read_write_wizard(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        # force=True → auth prompt shown; inputs: auth default, host default,
        # access default (read-write), projects, branch default.
        inputs = iter(["", "", "", "group/my-proj", ""])

        def _input(p: object) -> str:
            try:
                return next(inputs)
            except StopIteration:
                return ""

        monkeypatch.setattr("builtins.input", _input)
        secrets = iter(["glpat-readtoken", "glpat-writetoken"])
        monkeypatch.setattr("getpass.getpass", lambda p: next(secrets))
        setup.cmd_init(root, _interactive_args(force=True), Out(color=False))

        secrets_dir = root / ".catraz" / "secrets"
        assert (secrets_dir / "read_tokens").read_text() == "gitlab.com glpat-readtoken\n"
        assert (secrets_dir / "write_tokens").read_text() == "gitlab.com glpat-writetoken\n"
        assert _endpoints(root) == {("gitlab.com", "gitlab")}

        toml = root / ".catraz" / "config" / "warden.toml"
        assert _read_toml_allowed_projects(toml) == ["group/my-proj"]
        text = toml.read_text()
        assert re.search(r'branch_prefixes\s*=\s*\[\s*"claude/"\s*\]', text)
        assert not re.search(r"^\s*branch_prefix\s*=", text, re.M)

    def test_enter_on_default_prompts_both_tokens(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        monkeypatch.setattr("builtins.input", lambda p: "")
        calls: list[object] = []

        def _getpass(p: object) -> str:
            calls.append(p)
            return ""

        monkeypatch.setattr("getpass.getpass", _getpass)
        setup.cmd_init(root, _interactive_args(force=True), Out(color=False))
        assert len(calls) == 2, "read-write must prompt read and write tokens"


class TestInteractiveReadOnly:
    """Choosing read-only prompts only the read token; write_tokens stays empty."""

    def test_read_only_wizard(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        # auth default, host default, access "2" (read-only), projects, branch.
        inputs = iter(["", "", "2", "group/my-proj", ""])

        def _input(p: object) -> str:
            try:
                return next(inputs)
            except StopIteration:
                return ""

        monkeypatch.setattr("builtins.input", _input)
        calls: list[object] = []

        def _getpass(p: object) -> str:
            calls.append(p)
            return "glpat-readtoken"

        monkeypatch.setattr("getpass.getpass", _getpass)
        setup.cmd_init(root, _interactive_args(force=True), Out(color=False))

        secrets_dir = root / ".catraz" / "secrets"
        assert _read_grouped_token(secrets_dir, "read_tokens", "gitlab.com") == "glpat-readtoken"
        assert (secrets_dir / "write_tokens").read_text() == ""
        assert len(calls) == 1, "only the read token should be prompted"
        assert _read_toml_allowed_projects(root / ".catraz" / "config" / "warden.toml") == [
            "group/my-proj"
        ]

    def test_write_token_not_clobbered_when_existing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        secrets_dir = root / ".catraz" / "secrets"
        secrets_dir.mkdir(mode=0o700, exist_ok=True)
        (secrets_dir / "write_tokens").write_text("gitlab.com glpat-existing\n")
        (secrets_dir / "write_tokens").chmod(0o600)
        inputs = iter(["", "", "2", "", ""])  # read-only

        def _input(p: object) -> str:
            try:
                return next(inputs)
            except StopIteration:
                return ""

        monkeypatch.setattr("builtins.input", _input)
        monkeypatch.setattr("getpass.getpass", lambda p: "glpat-read")
        setup.cmd_init(root, _interactive_args(force=True), Out(color=False))
        assert (secrets_dir / "write_tokens").read_text() == "gitlab.com glpat-existing\n"


class TestInteractiveHost:
    """A custom host keys both the token line and the endpoint."""

    def test_custom_host(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        # auth default, host "gitlab.example.com", access "2" (read-only), projects, branch.
        inputs = iter(["", "gitlab.example.com", "2", "", ""])

        def _input(p: object) -> str:
            try:
                return next(inputs)
            except StopIteration:
                return ""

        monkeypatch.setattr("builtins.input", _input)
        monkeypatch.setattr("getpass.getpass", lambda p: "glpat-read")
        setup.cmd_init(root, _interactive_args(force=True), Out(color=False))

        secrets_dir = root / ".catraz" / "secrets"
        assert (
            _read_grouped_token(secrets_dir, "read_tokens", "gitlab.example.com") == "glpat-read"
        )
        assert _endpoints(root) == {("gitlab.example.com", "gitlab")}


class TestInteractiveAuthMode:
    """AUTH_MODE is prompted when absent from .env (or --force)."""

    def test_auth_mode_kept_when_present_and_no_force(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        monkeypatch.setattr("builtins.input", lambda p: "")
        monkeypatch.setattr("getpass.getpass", lambda p: "")
        setup.cmd_init(root, _interactive_args(force=False), Out(color=False))
        assert load_env(root / ".catraz" / ".env").get("AUTH_MODE") == "subscription"


# TOML setters — must round-trip against the real shipped template


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
        assert _read_toml_allowed_projects(toml) == ["group/proj-a", "group/proj-b"]

    def test_set_toml_list_preserves_inline_comment(self, tmp_path: Path) -> None:
        toml = self._copy_template(tmp_path)
        original_text = toml.read_text()
        set_toml_list(toml, "allowed_projects", ["group/proj"])
        new_text = toml.read_text()
        if "# R2:" in original_text:
            assert "# R2:" in new_text

    def test_set_toml_scalar_updates_branch_prefix(self, tmp_path: Path) -> None:
        toml = self._copy_template(tmp_path)
        set_toml_scalar(toml, "branch_prefix", "feat/")
        assert '"feat/"' in toml.read_text()

    def test_set_toml_scalar_preserves_comment(self, tmp_path: Path) -> None:
        toml = self._copy_template(tmp_path)
        original_text = toml.read_text()
        set_toml_scalar(toml, "branch_prefix", "feat/")
        if "# R2:" in original_text:
            assert "# R2:" in toml.read_text()

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
        toml = self._copy_template(tmp_path)
        set_toml_list(toml, "allowed_projects", ["mygroup/myproject"])
        assert _read_toml_allowed_projects(toml) == ["mygroup/myproject"]

    def test_set_toml_list_roundtrip_readable_by_function(self, tmp_path: Path) -> None:
        toml = self._copy_template(tmp_path)
        projects = ["a/b", "c/d/e", "x/y"]
        set_toml_list(toml, "allowed_projects", projects)
        assert _read_toml_allowed_projects(toml) == projects


# ensure_git_endpoint


class TestEnsureGitEndpoint:
    def test_appends_when_absent(self, tmp_path: Path) -> None:
        from catraz.policy import ensure_git_endpoint

        toml = tmp_path / "warden.toml"
        toml.write_text("[git.rules]\n")
        ensure_git_endpoint(toml, "gitlab.com", "gitlab")
        data = tomllib.loads(toml.read_text())
        assert data["git"]["endpoint"] == [{"host": "gitlab.com", "type": "gitlab"}]

    def test_idempotent_on_normalised_host(self, tmp_path: Path) -> None:
        from catraz.policy import ensure_git_endpoint

        toml = tmp_path / "warden.toml"
        toml.write_text('[[git.endpoint]]\nhost = "gitlab.com"\ntype = "gitlab"\n')
        ensure_git_endpoint(toml, "GitLab.com:443", "gitlab")
        data = tomllib.loads(toml.read_text())
        assert len(data["git"]["endpoint"]) == 1


# unset_env_keys


class TestUnsetEnvKeys:
    def test_removes_active_key(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("DEV_UID=1000\nWARDEN_ALLOWED_PROJECTS=group/proj\nAUTH_MODE=sub\n")
        unset_env_keys(env_file, ["WARDEN_ALLOWED_PROJECTS"])
        env = load_env(env_file)
        assert "WARDEN_ALLOWED_PROJECTS" not in env
        assert env.get("DEV_UID") == "1000"

    def test_leaves_comments_untouched(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("# WARDEN_ALLOWED_PROJECTS=\nWARDEN_ALLOWED_PROJECTS=group/proj\n")
        unset_env_keys(env_file, ["WARDEN_ALLOWED_PROJECTS"])
        text = env_file.read_text()
        assert "# WARDEN_ALLOWED_PROJECTS=" in text
        assert "WARDEN_ALLOWED_PROJECTS" not in load_env(env_file)

    def test_noop_when_key_absent(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("DEV_UID=1000\n")
        unset_env_keys(env_file, ["WARDEN_ALLOWED_PROJECTS"])
        assert load_env(env_file).get("DEV_UID") == "1000"

    def test_noop_when_file_missing(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.env"
        unset_env_keys(missing, ["FOO"])

    def test_removes_multiple_keys(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("A=1\nWARDEN_ALLOWED_PROJECTS=g/p\nWARDEN_BRANCH_PREFIX=claude/\nB=2\n")
        unset_env_keys(env_file, ["WARDEN_ALLOWED_PROJECTS", "WARDEN_BRANCH_PREFIX"])
        env = load_env(env_file)
        assert "WARDEN_ALLOWED_PROJECTS" not in env
        assert "WARDEN_BRANCH_PREFIX" not in env
        assert env.get("A") == "1"
        assert env.get("B") == "2"


# Base image wizard


class TestBaseImageWizard:
    """Base image config in interactive and --yes modes. The base Dockerfile is
    seeded to config/image/Dockerfile by cmd_init; there is no interactive
    base-image prompt. BASE_* remain .env power-user overrides via _wizard_yes."""

    def _run_interactive(
        self,
        root: Path,
        monkeypatch: pytest.MonkeyPatch,
        inputs: list[str],
        force: bool = False,
    ) -> dict[str, str]:
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

    def test_no_base_image_prompt_in_interactive(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_root(tmp_path)
        env = self._run_interactive(root, monkeypatch, [])
        assert "BASE_IMAGE" not in env
        assert "BASE_DOCKERFILE" not in env

    def test_local_dockerfile_seeded_by_init(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        monkeypatch.setattr("builtins.input", lambda p: "")
        monkeypatch.setattr("getpass.getpass", lambda p: "")
        setup.cmd_init(root, _interactive_args(), Out(color=False))
        df = root / ".catraz" / "config" / "image" / "Dockerfile"
        assert df.exists(), "config/image/Dockerfile must be seeded by init"
        assert "ubuntu:24.04" in df.read_text()

    def test_existing_base_image_env_preserved_on_interactive(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_root(tmp_path)
        env_path = root / ".catraz" / ".env"
        env_path.write_text("DEV_UID=1000\nAUTH_MODE=subscription\nBASE_IMAGE=python:3.11\n")
        env = self._run_interactive(root, monkeypatch, [], force=False)
        assert env.get("BASE_IMAGE") == "python:3.11"

    def test_yes_base_image_from_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        monkeypatch.setenv("BASE_IMAGE", "python:3.11")
        setup.cmd_init(root, _yes_args(), Out(color=False))
        assert load_env(root / ".catraz" / ".env").get("BASE_IMAGE") == "python:3.11"

    def test_yes_base_dockerfile_from_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_root(tmp_path)
        _patch_common(monkeypatch)
        monkeypatch.setenv("BASE_DOCKERFILE", "./Dockerfile")
        setup.cmd_init(root, _yes_args(), Out(color=False))
        assert load_env(root / ".catraz" / ".env").get("BASE_DOCKERFILE") == "./Dockerfile"

    def test_yes_base_image_takes_priority_over_dockerfile(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_root(tmp_path)
        env_path = root / ".catraz" / ".env"
        env_path.write_text("DEV_UID=1000\nAUTH_MODE=subscription\nBASE_DOCKERFILE=./Dockerfile\n")
        _patch_common(monkeypatch)
        monkeypatch.setenv("BASE_IMAGE", "img:1")
        monkeypatch.setenv("BASE_DOCKERFILE", "./Dockerfile")
        setup.cmd_init(root, _yes_args(), Out(color=False))
        env = load_env(env_path)
        assert env.get("BASE_IMAGE") == "img:1"
        assert "BASE_DOCKERFILE" not in env
