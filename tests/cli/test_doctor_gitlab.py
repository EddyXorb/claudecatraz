import urllib.error
from pathlib import Path
from typing import Any

import pytest

from catraz import doctor


# check_policy — the allowlist pre-check keys off configured endpoints now
# ("no endpoint configured" replaces the old GITLAB_MODE=off short-circuit);
# each endpoint's own allowed_projects is checked in isolation.


class TestCheckPolicy:
    def test_no_endpoint_allowlist_not_required(self, tmp_path: Path) -> None:
        f = doctor.Findings()
        doctor.check_policy(tmp_path, {}, f)
        assert not any(i[0] == doctor.BAD for i in f.items)
        assert any(i[0] == doctor.OK and "allowlist not required" in i[2] for i in f.items)

    def test_nonempty_allowlist_ok(self, tmp_path: Path) -> None:
        _write_endpoints(tmp_path, [("gitlab.com", "gitlab")], {"gitlab.com": ["group/project"]})
        f = doctor.Findings()
        doctor.check_policy(tmp_path, {}, f)
        assert not any(i[0] == doctor.BAD for i in f.items)
        assert any(i[0] == doctor.OK and "gitlab.com" in i[2] for i in f.items)

    def test_empty_allowlist_warns(self, tmp_path: Path) -> None:
        _write_endpoints(tmp_path, [("gitlab.com", "gitlab")])
        f = doctor.Findings()
        doctor.check_policy(tmp_path, {}, f)
        assert any(i[0] == doctor.WARN and "gitlab.com" in i[2] for i in f.items)
        assert not any(i[0] == doctor.BAD for i in f.items)

    def test_invalid_project_is_bad(self, tmp_path: Path) -> None:
        _write_endpoints(tmp_path, [("gitlab.com", "gitlab")], {"gitlab.com": ["leaf-name"]})
        f = doctor.Findings()
        doctor.check_policy(tmp_path, {}, f)
        assert any(i[0] == doctor.BAD and "gitlab.com" in i[2] for i in f.items)

    def test_allowlists_are_isolated_per_host(self, tmp_path: Path) -> None:
        """One host's populated allowlist must never mask another host's
        empty one — each endpoint is checked independently."""
        _write_endpoints(
            tmp_path,
            [("gitlab.com", "gitlab"), ("my-gitlab.de", "gitlab")],
            {"gitlab.com": ["group/project"]},
        )
        f = doctor.Findings()
        doctor.check_policy(tmp_path, {}, f)
        assert any(
            i[0] == doctor.OK and "gitlab.com" in i[2] and "allowed project" in i[2]
            for i in f.items
        )
        assert any(i[0] == doctor.WARN and "my-gitlab.de" in i[2] for i in f.items)


# Multi-endpoint token model: grouped read_tokens/write_tokens files
# cross-checked against [[git.endpoint]] in warden.toml.


def _write_grouped(root: Path, filename: str, tokens: dict[str, str]) -> None:
    """Write a grouped `<host> <token>` secrets file under .catraz/secrets/."""
    secrets_dir = root / ".catraz" / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"{host} {token}" for host, token in tokens.items()]
    (secrets_dir / filename).write_text("\n".join(lines) + ("\n" if lines else ""))


def _write_endpoints(
    root: Path,
    endpoints: list[tuple[str, str]],
    allowed_projects: dict[str, list[str]] | None = None,
) -> None:
    """Write a minimal warden.toml with one [[git.endpoint]] per (host, type),
    optionally seeding that host's own allowed_projects."""
    import json

    allowed_projects = allowed_projects or {}
    config_dir = root / ".catraz" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    lines = ["[git.rules]", ""]
    for host, etype in endpoints:
        lines.append("[[git.endpoint]]")
        lines.append(f'host = "{host}"')
        lines.append(f'type = "{etype}"')
        if host in allowed_projects:
            lines.append(f"allowed_projects = {json.dumps(allowed_projects[host])}")
        lines.append("")
    (config_dir / "warden.toml").write_text("\n".join(lines))


class TestParseGroupedTokens:
    """Same splitting rule as the Warden's Step 02 `_parse_token_file`."""

    def test_splits_on_first_whitespace(self) -> None:
        tokens = doctor._parse_grouped_tokens("gitlab.internal:8443  glpat-abc\n")
        assert tokens == {"gitlab.internal:8443": "glpat-abc"}

    def test_ignores_comments_and_blank_lines(self) -> None:
        text = "# comment\n\ngitlab.com glpat-x\n   \n"
        assert doctor._parse_grouped_tokens(text) == {"gitlab.com": "glpat-x"}

    def test_malformed_line_skipped(self) -> None:
        assert doctor._parse_grouped_tokens("nocolonnowhitespace\n") == {}

    def test_empty_text(self) -> None:
        assert doctor._parse_grouped_tokens("") == {}


class TestReadGitEndpoints:
    def test_reads_host_and_type(self, tmp_path: Path) -> None:
        _write_endpoints(tmp_path, [("gitlab.com", "gitlab"), ("git.internal", "plain")])
        endpoints = doctor._read_git_endpoints(tmp_path)
        assert {(e["host"], e["type"]) for e in endpoints} == {
            ("gitlab.com", "gitlab"),
            ("git.internal", "plain"),
        }

    def test_missing_warden_toml_returns_empty(self, tmp_path: Path) -> None:
        assert doctor._read_git_endpoints(tmp_path) == []

    def test_malformed_toml_returns_empty(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".catraz" / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "warden.toml").write_text("not [ valid toml")
        assert doctor._read_git_endpoints(tmp_path) == []


class TestCheckTokensCrossCheck:
    """The four scenarios from 06-cli-doctor-init.md's "Tests" section — all
    warn (or ok), never a BAD finding; doctor must never block startup."""

    def test_unlisted_host_token_warns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_endpoints(tmp_path, [("gitlab.com", "gitlab")])
        _write_grouped(
            tmp_path, "read_tokens", {"gitlab.com": "glpat-r", "typo.example": "glpat-t"}
        )
        _write_grouped(tmp_path, "write_tokens", {"gitlab.com": "glpat-w"})
        monkeypatch.setattr(doctor, "_probe_gitlab_tokens", lambda *a, **kw: None)
        f = doctor.Findings()
        doctor.check_tokens(tmp_path, {}, f)
        assert any(
            i[0] == doctor.WARN and "typo.example" in i[2] and "typo" in i[2] for i in f.items
        )
        assert not any(i[0] == doctor.BAD for i in f.items)

    def test_endpoint_without_token_warns_closed(self, tmp_path: Path) -> None:
        _write_endpoints(tmp_path, [("gitlab.com", "gitlab")])
        # No token files at all.
        f = doctor.Findings()
        doctor.check_tokens(tmp_path, {}, f)
        assert any(
            i[0] == doctor.WARN and "gitlab.com" in i[2] and "closed" in i[2] for i in f.items
        )
        assert not any(i[0] == doctor.BAD for i in f.items)

    def test_write_without_read_warns_least_privilege(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_endpoints(tmp_path, [("gitlab.com", "gitlab")])
        _write_grouped(tmp_path, "write_tokens", {"gitlab.com": "glpat-w"})
        monkeypatch.setattr(doctor, "_probe_gitlab_tokens", lambda *a, **kw: None)
        f = doctor.Findings()
        doctor.check_tokens(tmp_path, {}, f)
        assert any(
            i[0] == doctor.WARN and "gitlab.com" in i[2] and "read" in i[2].lower() for i in f.items
        )
        assert not any(i[0] == doctor.BAD for i in f.items)

    def test_clean_setup_no_warning(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write_endpoints(tmp_path, [("gitlab.com", "gitlab")])
        _write_grouped(tmp_path, "read_tokens", {"gitlab.com": "glpat-r"})
        _write_grouped(tmp_path, "write_tokens", {"gitlab.com": "glpat-w"})
        # Offline probing degrades to a warn, not a bad — skip the probe outright
        # so this test stays focused on the cross-check, not the probe's own warning.
        monkeypatch.setattr(doctor, "_probe_gitlab_tokens", lambda *a, **kw: None)
        f = doctor.Findings()
        doctor.check_tokens(tmp_path, {}, f)
        assert not any(i[0] == doctor.WARN for i in f.items)
        assert not any(i[0] == doctor.BAD for i in f.items)
        assert any(i[0] == doctor.OK and "gitlab.com" in i[2] for i in f.items)

    def test_no_endpoints_no_tokens_is_ok(self, tmp_path: Path) -> None:
        f = doctor.Findings()
        doctor.check_tokens(tmp_path, {}, f)
        assert not any(i[0] in (doctor.WARN, doctor.BAD) for i in f.items)
        assert any(i[0] == doctor.OK for i in f.items)

    def test_never_exits_bad_on_any_combination(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Umbrella check for 'Nicht tun': no combination of endpoints/tokens
        doctor can construct here ever produces a BAD finding by itself
        (BAD is reserved for an online 401/inactive-token probe result)."""
        _write_endpoints(tmp_path, [("gitlab.com", "gitlab"), ("plain.example", "plain")])
        _write_grouped(tmp_path, "read_tokens", {"typo.example": "x"})
        _write_grouped(tmp_path, "write_tokens", {"gitlab.com": "glpat-w"})
        monkeypatch.setattr(doctor, "_probe_gitlab_tokens", lambda *a, **kw: None)
        f = doctor.Findings()
        doctor.check_tokens(tmp_path, {}, f)
        assert not any(i[0] == doctor.BAD for i in f.items)


class TestProbeGitlabTokens:
    """Per-host probing — only `type = "gitlab"` endpoints are probed
    online; `plain` endpoints are skipped."""

    def test_only_gitlab_type_is_probed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        probed_hosts: list[str] = []

        def _fake_get(base: str, path: str, token: str, timeout: int = 5) -> Any:
            probed_hosts.append(base)
            raise urllib.error.URLError("offline")

        monkeypatch.setattr(doctor, "_gitlab_get", _fake_get)
        endpoints = [
            {"host": "gitlab.com", "type": "gitlab"},
            {"host": "plain.example", "type": "plain"},
        ]
        tokens = {"gitlab.com": "glpat-r", "plain.example": "sometoken"}
        f = doctor.Findings()
        doctor._probe_gitlab_tokens(endpoints, tokens, {}, f)
        assert probed_hosts == ["https://gitlab.com"]

    def test_offline_degrades_to_warn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(doctor, "_gitlab_get", _raise_url_error)
        endpoints = [{"host": "gitlab.com", "type": "gitlab"}]
        f = doctor.Findings()
        doctor._probe_gitlab_tokens(endpoints, {"gitlab.com": "glpat-r"}, {}, f)
        assert not any(i[0] == doctor.BAD for i in f.items)
        assert any(i[0] == doctor.WARN and "gitlab.com" in i[2] for i in f.items)

    def test_401_is_bad(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(doctor, "_gitlab_get", _http_error(401))
        endpoints = [{"host": "gitlab.com", "type": "gitlab"}]
        f = doctor.Findings()
        doctor._probe_gitlab_tokens(endpoints, {"gitlab.com": "glpat-r"}, {}, f)
        assert any(i[0] == doctor.BAD and "gitlab.com" in i[2] for i in f.items)

    def test_identical_read_write_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(doctor, "_gitlab_get", _raise_url_error)
        endpoints = [{"host": "gitlab.com", "type": "gitlab"}]
        f = doctor.Findings()
        doctor._probe_gitlab_tokens(endpoints, {"gitlab.com": "same"}, {"gitlab.com": "same"}, f)
        assert any(i[0] == doctor.WARN and "identical" in i[2] for i in f.items)


# _probe_write_user_read — the warden needs GET /user (write token) for R3,
# checked per-host: (host, base, token, f).


class TestProbeWriteUserRead:
    def test_user_read_403_is_bad(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(doctor, "_gitlab_get", _http_error(403))
        f = doctor.Findings()
        doctor._probe_write_user_read("gitlab.com", "https://gitlab.com", "glpat-w", f)
        assert any(
            i[0] == doctor.BAD and "GET /user" in i[2] and "gitlab.com" in i[2] for i in f.items
        )

    def test_user_read_401_is_bad(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(doctor, "_gitlab_get", _http_error(401))
        f = doctor.Findings()
        doctor._probe_write_user_read("gitlab.com", "https://gitlab.com", "glpat-w", f)
        assert any(i[0] == doctor.BAD for i in f.items)

    def test_user_read_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(doctor, "_gitlab_get", lambda *a, **kw: {"id": 42, "username": "bot"})
        f = doctor.Findings()
        doctor._probe_write_user_read("gitlab.com", "https://gitlab.com", "glpat-w", f)
        assert any(i[0] == doctor.OK and "service account" in i[2] for i in f.items)
        assert not any(i[0] == doctor.BAD for i in f.items)

    def test_user_read_offline_skips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(doctor, "_gitlab_get", _raise_url_error)
        f = doctor.Findings()
        doctor._probe_write_user_read("gitlab.com", "https://gitlab.com", "glpat-w", f)
        assert not any(i[0] == doctor.BAD for i in f.items)


# Private helpers


def _raise_url_error(*args: Any, **kwargs: Any) -> Any:
    raise urllib.error.URLError("offline")


def _http_error(code: int) -> "Any":
    def _raise(*args: Any, **kwargs: Any) -> Any:
        raise urllib.error.HTTPError(url="x", code=code, msg="x", hdrs=None, fp=None)  # type: ignore[arg-type]

    return _raise
