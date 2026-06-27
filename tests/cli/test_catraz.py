"""Unit tests for the pure logic in the `catraz` CLI.

Tests cover the parts that don't need Docker: project-path validation,
.env round-tripping, allowed_projects precedence, and service aliases.

Run:  python3 -m pytest tests/cli/ -q
"""

import pytest

from catraz import cli, envfile, policy
from catraz.compose import resolve_service, SERVICES
from catraz.cli import CliError


# ── validate_project ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "group/project",
    "group/sub/project",
    "untis-org/optimization-team/opt/opt-ci",
])
def test_validate_project_accepts_full_paths(path):
    assert policy.validate_project(path) is None


@pytest.mark.parametrize("path,fragment", [
    ("group/*", "wildcard"),
    ("group/**", "wildcard"),
    ("*-ci", "wildcard"),
    ("opt-ci", "full path"),          # leaf name → README's left-anchored trap
    ("", "empty"),
    ("/group/project", "slash"),
    ("group/project/", "slash"),
])
def test_validate_project_rejects_traps(path, fragment):
    reason = policy.validate_project(path)
    assert reason is not None
    assert fragment in reason


# ── load_env / set_env_values round-trip ────────────────────────────────────────

def test_load_env_strips_inline_comments(tmp_path):
    p = tmp_path / ".env"
    p.write_text(
        "ANTHROPIC_API_KEY=sk-test\n"
        "GITLAB_READ_TOKEN=glpat-x   # scopes: read_api\n"
        "# a comment line\n"
        "\n"
        "DEV_UID=1000\n"
    )
    env = envfile.load_env(p)
    assert env["ANTHROPIC_API_KEY"] == "sk-test"
    assert env["GITLAB_READ_TOKEN"] == "glpat-x"   # inline comment stripped
    assert env["DEV_UID"] == "1000"
    assert "a comment line" not in env


def test_set_env_values_uncomments_and_updates(tmp_path):
    p = tmp_path / ".env"
    p.write_text(
        "ANTHROPIC_API_KEY=\n"
        "# WARDEN_ALLOWED_PROJECTS=\n"
        "DEV_UID=1000\n"
    )
    envfile.set_env_values(p, {
        "ANTHROPIC_API_KEY": "sk-new",
        "WARDEN_ALLOWED_PROJECTS": "group/sub/a,group/sub/b",
    })
    env = envfile.load_env(p)
    assert env["ANTHROPIC_API_KEY"] == "sk-new"
    assert env["WARDEN_ALLOWED_PROJECTS"] == "group/sub/a,group/sub/b"
    # Exactly one active line each — no duplicate from the commented seed.
    active = [ln for ln in p.read_text().splitlines()
              if ln.startswith("WARDEN_ALLOWED_PROJECTS=")]
    assert len(active) == 1


def test_set_env_values_appends_absent_key(tmp_path):
    p = tmp_path / ".env"
    p.write_text("DEV_UID=1000\n")
    envfile.set_env_values(p, {"NEW_KEY": "value"})
    assert envfile.load_env(p)["NEW_KEY"] == "value"


# ── allowed_projects precedence (.env override wins over warden.toml) ────────────

def _project(tmp_path, env_override=None, toml_projects=None):
    (tmp_path / ".catraz" / "config").mkdir(parents=True, exist_ok=True)
    env = tmp_path / ".catraz" / ".env"
    lines = ["DEV_UID=1000"]
    if env_override is not None:
        lines.append(f"WARDEN_ALLOWED_PROJECTS={env_override}")
    env.write_text("\n".join(lines) + "\n")
    if toml_projects is not None:
        arr = ", ".join(f'"{x}"' for x in toml_projects)
        (tmp_path / ".catraz" / "config" / "warden.toml").write_text(
            f"allowed_projects = [{arr}]\n")
    return envfile.load_env(env)


def test_env_override_beats_toml(tmp_path, monkeypatch):
    monkeypatch.delenv("WARDEN_ALLOWED_PROJECTS", raising=False)
    env = _project(tmp_path, env_override="group/sub/from-env",
                   toml_projects=["group/sub/from-toml"])
    resolved, source = policy._resolve_allowed_projects(tmp_path, env)
    assert resolved == ["group/sub/from-env"]
    assert "override" in source


def test_toml_used_when_no_override(tmp_path, monkeypatch):
    monkeypatch.delenv("WARDEN_ALLOWED_PROJECTS", raising=False)
    env = _project(tmp_path, env_override=None,
                   toml_projects=["group/sub/a", "group/sub/b"])
    resolved, source = policy._resolve_allowed_projects(tmp_path, env)
    assert resolved == ["group/sub/a", "group/sub/b"]
    assert source == "warden.toml"


# ── service aliases ─────────────────────────────────────────────────────────────

def test_resolve_service_aliases():
    assert resolve_service("agent") == "claude-dev-env"
    assert resolve_service("warden") == "gitlab-warden"
    assert resolve_service("gitlab-warden") == "gitlab-warden"


def test_resolve_service_unknown_raises():
    with pytest.raises(CliError):
        resolve_service("nope")


# ── secret masking never leaks the full value ───────────────────────────────────

def test_mask_hides_value():
    assert envfile.mask("supersecrettoken").startswith("sup")
    assert "secrettoken"[3:] not in envfile.mask("supersecrettoken")
    assert envfile.mask("") == ""
