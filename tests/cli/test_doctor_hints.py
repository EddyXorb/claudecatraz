"""Findings whose fix is unambiguous carry that fix as a hint."""

from pathlib import Path

import pytest

from catraz import doctor


def _write(root: Path, rel: str, text: str) -> None:
    p = root / ".catraz" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _endpoint(root: Path, projects: str = '["g/p"]') -> None:
    _write(
        root,
        "config/warden.toml",
        f'[[git.endpoint]]\nhost = "gitlab.example"\ntype = "plain"\n'
        f"allowed_projects = {projects}\n",
    )


def _hint_for(f: doctor.Findings, fragment: str) -> str | None:
    matches = [i for i in f.items if fragment in i[2]]
    assert len(matches) == 1, f.items
    return matches[0][3]


def test_endpoint_without_token_hints_read_tokens_line(tmp_path: Path) -> None:
    _endpoint(tmp_path)
    f = doctor.Findings()
    doctor.check_tokens(tmp_path, {}, f)
    hint = _hint_for(f, "has no token")
    assert hint is not None and "gitlab.example <token>" in hint
    assert "secrets/read_tokens" in hint


def test_write_without_read_hints_read_tokens_line(tmp_path: Path) -> None:
    _endpoint(tmp_path)
    _write(tmp_path, "secrets/write_tokens", "gitlab.example glpat-w\n")
    f = doctor.Findings()
    doctor.check_tokens(tmp_path, {}, f)
    hint = _hint_for(f, "no read token")
    assert hint is not None and "secrets/read_tokens" in hint


def test_token_for_unknown_host_hints_spelling(tmp_path: Path) -> None:
    _endpoint(tmp_path)
    _write(tmp_path, "secrets/read_tokens", "gitlab.exmaple glpat-r\n")
    f = doctor.Findings()
    doctor.check_tokens(tmp_path, {}, f)
    hint = _hint_for(f, "matches no [[git.endpoint]]")
    assert hint is not None and "warden.toml" in hint


def test_empty_allowed_projects_hints_warden_toml(tmp_path: Path) -> None:
    _endpoint(tmp_path, projects="[]")
    f = doctor.Findings()
    doctor.check_policy(tmp_path, {}, f)
    hint = _hint_for(f, "allowed_projects empty")
    assert hint is not None and "allowed_projects" in hint and "warden.toml" in hint


def test_subscription_with_api_key_names_both_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, "secrets/anthropic_api_key", "sk-x\n")
    monkeypatch.setattr("catraz.agents.effective_credentials_mode", lambda root, env: "persistent")
    f = doctor.Findings()
    doctor.check_auth(tmp_path, {}, f)
    hint = _hint_for(f, "ANTHROPIC_API_KEY set")
    assert hint is not None and "anthropic_api_key" in hint and ".env" in hint
