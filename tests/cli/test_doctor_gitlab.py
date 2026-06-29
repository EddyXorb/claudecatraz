import urllib.error
from pathlib import Path
from typing import Any

import pytest

from catraz import doctor


# ---------------------------------------------------------------------------
# Existing tests (preserved)
# ---------------------------------------------------------------------------

def test_check_gitlab_url_set() -> None:
    f = doctor.Findings()
    doctor.check_gitlab({"GITLAB_URL": "https://gitlab.example.com"}, f)
    assert any(i[0] == doctor.OK and "gitlab.example.com" in i[2]
               for i in f.items)


def test_check_gitlab_url_unset() -> None:
    f = doctor.Findings()
    doctor.check_gitlab({}, f)
    assert any(i[0] == doctor.WARN and "GITLAB_URL" in i[2]
               for i in f.items)


def test_check_gitlab_url_empty() -> None:
    f = doctor.Findings()
    doctor.check_gitlab({"GITLAB_URL": ""}, f)
    assert any(i[0] == doctor.WARN for i in f.items)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_secrets(tmp_path: Path, *, read_token: str = "", write_token: str = "") -> Path:
    """Write token files under tmp_path/.catraz/secrets/."""
    secrets_dir = tmp_path / ".catraz" / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    for filename, value in [("gitlab_read_token", read_token),
                             ("gitlab_write_token", write_token)]:
        (secrets_dir / filename).write_text(value)
    return tmp_path


# ---------------------------------------------------------------------------
# GITLAB_MODE=off
# ---------------------------------------------------------------------------

class TestGitLabModeOff:
    """With GITLAB_MODE=off the doctor must produce no bad findings from
    tokens or policy, regardless of whether tokens/allowlist are set."""

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

    def test_check_tokens_no_bad(self, tmp_path: Path) -> None:
        _make_secrets(tmp_path, read_token="", write_token="")
        f = doctor.Findings()
        doctor.check_tokens(tmp_path, {"GITLAB_MODE": "off"}, f)
        assert not any(i[0] == doctor.BAD for i in f.items)
        assert any(i[0] == doctor.OK and "not required" in i[2] for i in f.items)

    def test_check_tokens_probe_not_called(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Probe must not be attempted in off mode."""
        _make_secrets(tmp_path, read_token="", write_token="")

        def _fail(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("_gitlab_get must not be called in off mode")

        monkeypatch.setattr(doctor, "_gitlab_get", _fail)
        f = doctor.Findings()
        doctor.check_tokens(tmp_path, {"GITLAB_MODE": "off"}, f)
        # If we got here without AssertionError, the probe was not called.
        assert not any(i[0] == doctor.BAD for i in f.items)

    def test_check_policy_no_bad_empty_allowlist(self, tmp_path: Path) -> None:
        """Empty allowed_projects must not be bad when GitLab is off."""
        # No warden.toml → _resolve_allowed_projects will return an empty list.
        f = doctor.Findings()
        doctor.check_policy(tmp_path, {"GITLAB_MODE": "off"}, f)
        assert not any(i[0] == doctor.BAD for i in f.items)
        assert any(i[0] == doctor.OK and "allowlist not required" in i[2]
                   for i in f.items)


# ---------------------------------------------------------------------------
# GITLAB_MODE=read-only
# ---------------------------------------------------------------------------

class TestGitLabModeReadOnly:
    """read-only mode: read token required, write token optional (ignored if present)."""

    def test_read_token_set_no_bad(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _make_secrets(tmp_path, read_token="glpat-readtoken", write_token="")
        # Offline: probe will hit a URLError → warn, not bad.
        monkeypatch.setattr(doctor, "_gitlab_get", _raise_url_error)
        f = doctor.Findings()
        doctor.check_tokens(tmp_path, {"GITLAB_MODE": "read-only"}, f)
        assert not any(i[0] == doctor.BAD for i in f.items)

    def test_empty_read_token_is_bad(self, tmp_path: Path) -> None:
        _make_secrets(tmp_path, read_token="", write_token="")
        f = doctor.Findings()
        doctor.check_tokens(tmp_path, {"GITLAB_MODE": "read-only"}, f)
        assert any(i[0] == doctor.BAD and "gitlab_read_token" in i[2]
                   for i in f.items)

    def test_write_token_present_warns(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A write token that is set when mode=read-only must emit a warn."""
        _make_secrets(tmp_path, read_token="glpat-readtoken",
                      write_token="glpat-writetoken")
        monkeypatch.setattr(doctor, "_gitlab_get", _raise_url_error)
        f = doctor.Findings()
        doctor.check_tokens(tmp_path, {"GITLAB_MODE": "read-only"}, f)
        assert any(i[0] == doctor.WARN and "read-only" in i[2] for i in f.items)
        assert not any(i[0] == doctor.BAD for i in f.items)

    def test_probe_only_read_token(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """In read-only mode only the read token is probed."""
        _make_secrets(tmp_path, read_token="glpat-readtoken", write_token="")
        probed: list[str] = []

        def _fake_get(base: str, path: str, token: str, timeout: int = 5) -> Any:
            probed.append(token)
            raise urllib.error.URLError("offline")

        monkeypatch.setattr(doctor, "_gitlab_get", _fake_get)
        f = doctor.Findings()
        doctor.check_tokens(tmp_path, {"GITLAB_MODE": "read-only"}, f)
        assert probed == ["glpat-readtoken"]

    def test_check_policy_nonempty_allowlist_ok(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-empty allowlist must be ok in read-only mode."""
        _mock_allowlist(tmp_path, monkeypatch, ["group/project"])
        f = doctor.Findings()
        doctor.check_policy(tmp_path, {"GITLAB_MODE": "read-only"}, f)
        assert not any(i[0] == doctor.BAD for i in f.items)
        assert any(i[0] == doctor.OK for i in f.items)

    def test_check_policy_empty_allowlist_warns(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty allowlist in read-only: a warning, not bad — the stack still boots
        and denies every GitLab op until a project is added."""
        _mock_allowlist(tmp_path, monkeypatch, [])
        f = doctor.Findings()
        doctor.check_policy(tmp_path, {"GITLAB_MODE": "read-only"}, f)
        assert any(i[0] == doctor.WARN for i in f.items)
        assert not any(i[0] == doctor.BAD for i in f.items)


# ---------------------------------------------------------------------------
# GITLAB_MODE=read-write (default — preserves existing contract)
# ---------------------------------------------------------------------------

class TestGitLabModeReadWrite:
    """read-write is the default; empty tokens/allowlist must still be bad."""

    def test_empty_read_token_bad(self, tmp_path: Path) -> None:
        _make_secrets(tmp_path, read_token="", write_token="glpat-writetoken")
        f = doctor.Findings()
        doctor.check_tokens(tmp_path, {"GITLAB_MODE": "read-write"}, f)
        assert any(i[0] == doctor.BAD and "gitlab_read_token" in i[2]
                   for i in f.items)

    def test_empty_write_token_bad(self, tmp_path: Path) -> None:
        _make_secrets(tmp_path, read_token="glpat-readtoken", write_token="")
        f = doctor.Findings()
        doctor.check_tokens(tmp_path, {"GITLAB_MODE": "read-write"}, f)
        assert any(i[0] == doctor.BAD and "gitlab_write_token" in i[2]
                   for i in f.items)

    def test_both_tokens_set_probes_both(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """In read-write mode, _probe_gitlab_tokens is called with both token pairs."""
        _make_secrets(tmp_path, read_token="glpat-readtoken",
                      write_token="glpat-writetoken")
        probe_calls: list[Any] = []

        def _fake_probe(root: Path, env: dict[str, str], f: Any, tokens: Any = None) -> None:
            probe_calls.append(tokens)

        monkeypatch.setattr(doctor, "_probe_gitlab_tokens", _fake_probe)
        f = doctor.Findings()
        doctor.check_tokens(tmp_path, {"GITLAB_MODE": "read-write"}, f)
        assert len(probe_calls) == 1
        labels = [label for label, _ in probe_calls[0]]
        assert "read" in labels
        assert "write" in labels

    def test_mode_absent_defaults_to_read_write(self, tmp_path: Path) -> None:
        """No GITLAB_MODE key in env means read-write (no regression)."""
        _make_secrets(tmp_path, read_token="", write_token="")
        f = doctor.Findings()
        doctor.check_tokens(tmp_path, {}, f)
        assert any(i[0] == doctor.BAD for i in f.items)

    def test_empty_allowlist_warns(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """read-write: empty allowlist warns (deny-all) — not bad, the stack boots."""
        _mock_allowlist(tmp_path, monkeypatch, [])
        f = doctor.Findings()
        doctor.check_policy(tmp_path, {"GITLAB_MODE": "read-write"}, f)
        assert any(i[0] == doctor.WARN for i in f.items)
        assert not any(i[0] == doctor.BAD for i in f.items)

    def test_check_gitlab_url_unset_warns(self) -> None:
        """URL-unset warning still fires in read-write mode."""
        f = doctor.Findings()
        doctor.check_gitlab({"GITLAB_MODE": "read-write"}, f)
        assert any(i[0] == doctor.WARN and "GITLAB_URL" in i[2] for i in f.items)


# ---------------------------------------------------------------------------
# _gitlab_mode helper
# ---------------------------------------------------------------------------

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
# Private helpers
# ---------------------------------------------------------------------------

def _raise_url_error(*args: Any, **kwargs: Any) -> Any:
    raise urllib.error.URLError("offline")


def _mock_allowlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, projects: list[str]) -> None:
    """Patch _resolve_allowed_projects to return the given list."""
    import catraz.policy as policy_mod
    monkeypatch.setattr(policy_mod, "_resolve_allowed_projects",
                        lambda root, env: (projects, "mock"))
