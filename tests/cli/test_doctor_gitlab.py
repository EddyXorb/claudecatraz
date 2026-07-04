import urllib.error
from pathlib import Path
from typing import Any

import pytest

from catraz import doctor


# ---------------------------------------------------------------------------
# check_gitlab / _gitlab_mode — GITLAB_URL/.env informational checks. Untouched
# by the multi-endpoint cutover (§05-env-cleanup.md is a separate, later step).
# ---------------------------------------------------------------------------


def test_check_gitlab_url_set() -> None:
    f = doctor.Findings()
    doctor.check_gitlab({"GITLAB_URL": "https://gitlab.example.com"}, f)
    assert any(i[0] == doctor.OK and "gitlab.example.com" in i[2] for i in f.items)


def test_check_gitlab_url_unset() -> None:
    f = doctor.Findings()
    doctor.check_gitlab({}, f)
    assert any(i[0] == doctor.WARN and "GITLAB_URL" in i[2] for i in f.items)


def test_check_gitlab_url_empty() -> None:
    f = doctor.Findings()
    doctor.check_gitlab({"GITLAB_URL": ""}, f)
    assert any(i[0] == doctor.WARN for i in f.items)


class TestGitLabModeOff:
    def test_check_gitlab_returns_ok(self) -> None:
        f = doctor.Findings()
        doctor.check_gitlab({"GITLAB_MODE": "off"}, f)
        assert any(i[0] == doctor.OK and "GITLAB_MODE=off" in i[2] for i in f.items)
        assert not any(i[0] == doctor.BAD for i in f.items)

    def test_check_gitlab_no_url_nag(self) -> None:
        """GITLAB_URL being unset must not warn when mode=off."""
        f = doctor.Findings()
        doctor.check_gitlab({"GITLAB_MODE": "off"}, f)
        assert not any("GITLAB_URL" in i[2] for i in f.items)

    def test_check_policy_no_bad_empty_allowlist(self, tmp_path: Path) -> None:
        """Empty allowed_projects must not be bad when GitLab is off."""
        f = doctor.Findings()
        doctor.check_policy(tmp_path, {"GITLAB_MODE": "off"}, f)
        assert not any(i[0] == doctor.BAD for i in f.items)
        assert any(i[0] == doctor.OK and "allowlist not required" in i[2] for i in f.items)


class TestGitLabModePolicy:
    """check_policy is untouched by the token/endpoint cutover — still keyed
    off GITLAB_MODE and the (separately-owned) allowed_projects resolution."""

    def test_check_policy_nonempty_allowlist_ok(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_allowlist(tmp_path, monkeypatch, ["group/project"])
        f = doctor.Findings()
        doctor.check_policy(tmp_path, {"GITLAB_MODE": "read-only"}, f)
        assert not any(i[0] == doctor.BAD for i in f.items)
        assert any(i[0] == doctor.OK for i in f.items)

    def test_check_policy_empty_allowlist_warns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_allowlist(tmp_path, monkeypatch, [])
        f = doctor.Findings()
        doctor.check_policy(tmp_path, {"GITLAB_MODE": "read-write"}, f)
        assert any(i[0] == doctor.WARN for i in f.items)
        assert not any(i[0] == doctor.BAD for i in f.items)

    def test_check_gitlab_url_unset_warns(self) -> None:
        f = doctor.Findings()
        doctor.check_gitlab({"GITLAB_MODE": "read-write"}, f)
        assert any(i[0] == doctor.WARN and "GITLAB_URL" in i[2] for i in f.items)


class TestGitLabModeHelper:
    def test_absent_defaults_read_write(self) -> None:
        assert doctor._gitlab_mode({}) == "read-write"

    def test_empty_defaults_read_write(self) -> None:
        assert doctor._gitlab_mode({"GITLAB_MODE": ""}) == "read-write"

    def test_off(self) -> None:
        assert doctor._gitlab_mode({"GITLAB_MODE": "off"}) == "off"

    def test_read_only(self) -> None:
        assert doctor._gitlab_mode({"GITLAB_MODE": "read-only"}) == "read-only"

    def test_strips_whitespace(self) -> None:
        assert doctor._gitlab_mode({"GITLAB_MODE": "  off  "}) == "off"


# ---------------------------------------------------------------------------
# Multi-endpoint token model (§4, §6): grouped read_tokens/write_tokens files
# cross-checked against [[git.endpoint]] in warden.toml. Same rules/wording as
# the Warden's Step 02 access_mode() derivation (warden/warden/core/config.py).
# ---------------------------------------------------------------------------


def _write_grouped(root: Path, filename: str, tokens: dict[str, str]) -> None:
    """Write a grouped `<host> <token>` secrets file under .catraz/secrets/."""
    secrets_dir = root / ".catraz" / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"{host} {token}" for host, token in tokens.items()]
    (secrets_dir / filename).write_text("\n".join(lines) + ("\n" if lines else ""))


def _write_endpoints(root: Path, endpoints: list[tuple[str, str]]) -> None:
    """Write a minimal warden.toml with one [[git.endpoint]] per (host, type)."""
    config_dir = root / ".catraz" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    lines = ["[git.rules]", ""]
    for host, etype in endpoints:
        lines.append("[[git.endpoint]]")
        lines.append(f'host = "{host}"')
        lines.append(f'type = "{etype}"')
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
        # Offline: probing degrades to a warn (unreachable), not a bad — but we
        # don't want the *probe's* offline-degrade warning to be mistaken for a
        # cross-check inconsistency, so keep this test focused on the
        # cross-check by skipping the probe outright.
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
    """Per-host probing (§6 point 3) — only `type = "gitlab"` endpoints are
    probed online; `plain` endpoints are skipped."""

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


# ---------------------------------------------------------------------------
# _probe_write_user_read — the warden needs GET /user (write token) for R3.
# Lifted to be per-host: (host, base, token, f) instead of reading the old
# fixed gitlab_write_token file itself.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _raise_url_error(*args: Any, **kwargs: Any) -> Any:
    raise urllib.error.URLError("offline")


def _http_error(code: int) -> "Any":
    def _raise(*args: Any, **kwargs: Any) -> Any:
        raise urllib.error.HTTPError(url="x", code=code, msg="x", hdrs=None, fp=None)  # type: ignore[arg-type]

    return _raise


def _mock_allowlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, projects: list[str]) -> None:
    """Patch _resolve_allowed_projects to return the given list."""
    import catraz.policy as policy_mod

    monkeypatch.setattr(policy_mod, "_resolve_allowed_projects", lambda root: (projects, "mock"))
