"""`doctor`'s action/token coherence cross-checks — a static, host-side parse
of `warden.toml` + the grouped token files, in the style of
`tests/cli/test_doctor_gitlab.py`. All findings here are WARN, never BAD/fail:
action coherence problems are not security problems."""

from __future__ import annotations

from pathlib import Path

from catraz import doctor


def _write_config(root: Path, toml_body: str) -> None:
    config_dir = root / ".catraz" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "warden.toml").write_text(toml_body)


def _write_grouped(root: Path, filename: str, tokens: dict[str, str]) -> None:
    secrets_dir = root / ".catraz" / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"{host} {token}" for host, token in tokens.items()]
    (secrets_dir / filename).write_text("\n".join(lines) + ("\n" if lines else ""))


# _effective_actions_for_host / _actions_valid_for_type — the reimplemented
# cascade, mirroring warden.core.config.Config.effective_actions.


class TestEffectiveActionsCascade:
    def test_endpoint_override_wins_unfiltered(self) -> None:
        endpoint = {"type": "plain", "actions": ("project.mr.create",)}
        # Explicit override is returned as-is even though "plain" can't actually
        # support project.mr.create — that's the warden loader's ConfigError to raise.
        assert doctor._effective_actions_for_host(None, endpoint) == ("project.mr.create",)

    def test_domain_default_used_when_no_override(self) -> None:
        endpoint = {"type": "gitlab", "actions": None}
        assert doctor._effective_actions_for_host(
            ("repo.read", "project.mr.comment"), endpoint
        ) == ("repo.read", "project.mr.comment")

    def test_builtin_default_used_when_nothing_set(self) -> None:
        endpoint = {"type": "gitlab", "actions": None}
        assert doctor._effective_actions_for_host(None, endpoint) == doctor.DEFAULT_ACTIONS

    def test_plain_type_cuts_inherited_forge_actions(self) -> None:
        endpoint = {"type": "plain", "actions": None}
        assert doctor._effective_actions_for_host(None, endpoint) == (
            "repo.read",
            "repo.branch.create",
            "repo.branch.push",
        )

    def test_gitlab_type_keeps_full_inherited_default(self) -> None:
        endpoint = {"type": "gitlab", "actions": None}
        assert doctor._effective_actions_for_host(None, endpoint) == doctor.DEFAULT_ACTIONS

    def test_unknown_type_falls_back_permissive_without_crashing(self) -> None:
        endpoint = {"type": "not-a-real-type", "actions": None}
        assert doctor._effective_actions_for_host(None, endpoint) == doctor.DEFAULT_ACTIONS


class TestReadGitActionsDefault:
    def test_reads_domain_actions(self, tmp_path: Path) -> None:
        _write_config(
            tmp_path,
            '[git]\nactions = ["repo.read", "project.mr.comment"]\n',
        )
        assert doctor._read_git_actions_default(tmp_path) == ("repo.read", "project.mr.comment")

    def test_absent_key_is_none(self, tmp_path: Path) -> None:
        _write_config(tmp_path, "[git.rules]\n")
        assert doctor._read_git_actions_default(tmp_path) is None

    def test_missing_file_is_none(self, tmp_path: Path) -> None:
        assert doctor._read_git_actions_default(tmp_path) is None


class TestReadGitEndpointsActions:
    def test_reads_per_endpoint_actions(self, tmp_path: Path) -> None:
        _write_config(
            tmp_path,
            '[[git.endpoint]]\nhost = "gitlab.com"\ntype = "gitlab"\n'
            'actions = ["repo.read", "project.mr.comment"]\n',
        )
        endpoints = doctor._read_git_endpoints(tmp_path)
        assert endpoints[0]["actions"] == ("repo.read", "project.mr.comment")

    def test_no_actions_key_is_none(self, tmp_path: Path) -> None:
        _write_config(tmp_path, '[[git.endpoint]]\nhost = "gitlab.com"\ntype = "gitlab"\n')
        endpoints = doctor._read_git_endpoints(tmp_path)
        assert endpoints[0]["actions"] is None


# check_action_coherence — the per-host warnings.


class TestCheckActionCoherence:
    def test_write_action_without_write_token_warns(self, tmp_path: Path) -> None:
        _write_config(
            tmp_path,
            '[[git.endpoint]]\nhost = "gitlab.com"\ntype = "gitlab"\n'
            'actions = ["repo.read", "project.mr.comment"]\n',
        )
        # No write_tokens file at all -> project.mr.comment is a write action with no token.
        f = doctor.Findings()
        doctor.check_action_coherence(tmp_path, {}, f)
        assert any(
            i[0] == doctor.WARN
            and "gitlab.com" in i[2]
            and "project.mr.comment" in i[2]
            and "write_token" in i[2]
            for i in f.items
        )
        assert not any(i[0] == doctor.BAD for i in f.items)

    def test_write_token_present_silences_write_action_warning(self, tmp_path: Path) -> None:
        _write_config(
            tmp_path,
            '[[git.endpoint]]\nhost = "gitlab.com"\ntype = "gitlab"\n'
            'actions = ["repo.read", "project.mr.comment"]\n',
        )
        _write_grouped(tmp_path, "write_tokens", {"gitlab.com": "glpat-w"})
        f = doctor.Findings()
        doctor.check_action_coherence(tmp_path, {}, f)
        assert not any(i[0] == doctor.WARN and "write_token" in i[2] for i in f.items)
        assert not any(i[0] == doctor.BAD for i in f.items)

    def test_repo_read_only_never_warns_about_write_token(self, tmp_path: Path) -> None:
        _write_config(
            tmp_path,
            '[[git.endpoint]]\nhost = "gitlab.com"\ntype = "gitlab"\nactions = ["repo.read"]\n',
        )
        f = doctor.Findings()
        doctor.check_action_coherence(tmp_path, {}, f)
        assert not any(i[0] == doctor.WARN for i in f.items)
        assert not any(i[0] == doctor.BAD for i in f.items)

    def test_mr_create_without_branch_write_warns(self, tmp_path: Path) -> None:
        _write_config(
            tmp_path,
            '[[git.endpoint]]\nhost = "gitlab.com"\ntype = "gitlab"\n'
            'actions = ["repo.read", "project.mr.create"]\n',
        )
        _write_grouped(tmp_path, "write_tokens", {"gitlab.com": "glpat-w"})
        f = doctor.Findings()
        doctor.check_action_coherence(tmp_path, {}, f)
        assert any(
            i[0] == doctor.WARN
            and "gitlab.com" in i[2]
            and "project.mr.create" in i[2]
            and "repo.branch" in i[2]
            for i in f.items
        )
        assert not any(i[0] == doctor.BAD for i in f.items)

    def test_mr_create_with_branch_push_no_warning(self, tmp_path: Path) -> None:
        _write_config(
            tmp_path,
            '[[git.endpoint]]\nhost = "gitlab.com"\ntype = "gitlab"\n'
            'actions = ["repo.read", "repo.branch.push", "project.mr.create"]\n',
        )
        _write_grouped(tmp_path, "write_tokens", {"gitlab.com": "glpat-w"})
        f = doctor.Findings()
        doctor.check_action_coherence(tmp_path, {}, f)
        assert not any(
            "project.mr.create" in i[2] and "repo.branch" in i[2]
            for i in f.items
            if i[0] == doctor.WARN
        )

    def test_ci_trigger_without_branch_write_warns(self, tmp_path: Path) -> None:
        _write_config(
            tmp_path,
            '[[git.endpoint]]\nhost = "gitlab.com"\ntype = "gitlab"\n'
            'actions = ["repo.read", "project.ci.trigger"]\n',
        )
        _write_grouped(tmp_path, "write_tokens", {"gitlab.com": "glpat-w"})
        f = doctor.Findings()
        doctor.check_action_coherence(tmp_path, {}, f)
        assert any(
            i[0] == doctor.WARN
            and "gitlab.com" in i[2]
            and "project.ci.trigger" in i[2]
            and "repo.branch" in i[2]
            for i in f.items
        )
        assert not any(i[0] == doctor.BAD for i in f.items)

    def test_dead_quota_is_silent(self, tmp_path: Path) -> None:
        """max_open_mrs set without project.mr.create is harmless — no warning at all."""
        _write_config(
            tmp_path,
            "[git.rules]\nmax_open_mrs = 5\n\n"
            '[[git.endpoint]]\nhost = "gitlab.com"\ntype = "gitlab"\n'
            'actions = ["repo.read", "repo.branch.push"]\n',
        )
        _write_grouped(tmp_path, "write_tokens", {"gitlab.com": "glpat-w"})
        f = doctor.Findings()
        doctor.check_action_coherence(tmp_path, {}, f)
        assert not any(i[0] in (doctor.WARN, doctor.BAD) for i in f.items)

    def test_gitlab_mode_off_short_circuits(self, tmp_path: Path) -> None:
        _write_config(
            tmp_path,
            '[[git.endpoint]]\nhost = "gitlab.com"\ntype = "gitlab"\n'
            'actions = ["project.mr.comment"]\n',
        )
        f = doctor.Findings()
        doctor.check_action_coherence(tmp_path, {"GITLAB_MODE": "off"}, f)
        assert f.items == []

    def test_no_endpoints_no_findings(self, tmp_path: Path) -> None:
        f = doctor.Findings()
        doctor.check_action_coherence(tmp_path, {}, f)
        assert f.items == []

    def test_never_produces_bad(self, tmp_path: Path) -> None:
        """Action coherence is never a BAD finding, even with several warnings stacked."""
        _write_config(
            tmp_path,
            '[[git.endpoint]]\nhost = "gitlab.com"\ntype = "gitlab"\n'
            'actions = ["repo.read", "project.mr.create", "project.ci.trigger"]\n\n'
            '[[git.endpoint]]\nhost = "plain.example"\ntype = "plain"\n',
        )
        f = doctor.Findings()
        doctor.check_action_coherence(tmp_path, {}, f)
        assert not any(i[0] == doctor.BAD for i in f.items)
