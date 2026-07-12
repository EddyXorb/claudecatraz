"""Unit tests for the pure logic in the `catraz` CLI: project-path
validation, .env round-tripping, allowed_projects precedence, and service
aliases — the parts that don't need Docker."""

from pathlib import Path
import pytest

from catraz import envfile, policy
from catraz.compose import resolve_service
from catraz.errors import CliError


# ── validate_project ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "group/project",
        "group/sub/project",
        "my_group/my_project",
    ],
)
def test_validate_project_accepts_full_paths(path: str) -> None:
    assert policy.validate_project(path) is None


@pytest.mark.parametrize(
    "path,fragment",
    [
        ("group/*", "wildcard"),
        ("group/**", "wildcard"),
        ("*-ci", "wildcard"),
        ("opt-ci", "full path"),  # leaf name → README's left-anchored trap
        ("", "empty"),
        ("/group/project", "slash"),
        ("group/project/", "slash"),
    ],
)
def test_validate_project_rejects_traps(path: str, fragment: str) -> None:
    reason = policy.validate_project(path)
    assert reason is not None
    assert fragment in reason


# ── load_env / set_env_values round-trip ────────────────────────────────────────


def test_load_env_strips_inline_comments(tmp_path: Path) -> None:
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
    assert env["GITLAB_READ_TOKEN"] == "glpat-x"  # inline comment stripped
    assert env["DEV_UID"] == "1000"
    assert "a comment line" not in env


def test_set_env_values_uncomments_and_updates(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text("ANTHROPIC_API_KEY=\n# WARDEN_ALLOWED_PROJECTS=\nDEV_UID=1000\n")
    envfile.set_env_values(
        p,
        {
            "ANTHROPIC_API_KEY": "sk-new",
            "WARDEN_ALLOWED_PROJECTS": "group/sub/a,group/sub/b",
        },
    )
    env = envfile.load_env(p)
    assert env["ANTHROPIC_API_KEY"] == "sk-new"
    assert env["WARDEN_ALLOWED_PROJECTS"] == "group/sub/a,group/sub/b"
    # Exactly one active line each — no duplicate from the commented seed.
    active = [ln for ln in p.read_text().splitlines() if ln.startswith("WARDEN_ALLOWED_PROJECTS=")]
    assert len(active) == 1


def test_set_env_values_appends_absent_key(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text("DEV_UID=1000\n")
    envfile.set_env_values(p, {"NEW_KEY": "value"})
    assert envfile.load_env(p)["NEW_KEY"] == "value"


# ── allowed_projects resolution (single source: the endpoint's own entry
# in warden.toml) ────────────────────────────────────────────────────────


def _project(tmp_path: Path, toml_projects: list[str] | None = None, host: str = "gitlab.com") -> None:
    config = tmp_path / ".catraz" / "config"
    config.mkdir(parents=True, exist_ok=True)
    if toml_projects is not None:
        arr = ", ".join(f'"{x}"' for x in toml_projects)
        (config / "warden.toml").write_text(
            f'[[git.endpoint]]\nhost = "{host}"\ntype = "gitlab"\nallowed_projects = [{arr}]\n'
        )


def test_resolves_allowed_projects_from_toml(tmp_path: Path) -> None:
    _project(tmp_path, toml_projects=["group/sub/a", "group/sub/b"])
    resolved, source = policy._resolve_allowed_projects(tmp_path, "gitlab.com")
    assert resolved == ["group/sub/a", "group/sub/b"]
    assert source == "warden.toml"


def test_resolves_empty_for_unconfigured_host(tmp_path: Path) -> None:
    _project(tmp_path, toml_projects=["group/sub/a"], host="gitlab.com")
    resolved, source = policy._resolve_allowed_projects(tmp_path, "other-host.example")
    assert resolved == []
    assert source == "warden.toml"


def test_resolves_empty_without_warden_toml(tmp_path: Path) -> None:
    resolved, source = policy._resolve_allowed_projects(tmp_path, "gitlab.com")
    assert resolved == []
    assert source == "no warden.toml"


# ── service aliases ─────────────────────────────────────────────────────────────


def test_resolve_service_aliases() -> None:
    assert resolve_service("agent") == "claude-dev-env"
    assert resolve_service("warden") == "gitlab-warden"
    assert resolve_service("gitlab-warden") == "gitlab-warden"


def test_resolve_service_unknown_raises() -> None:
    with pytest.raises(CliError):
        resolve_service("nope")


# ── secret masking never leaks the full value ───────────────────────────────────


def test_mask_hides_value() -> None:
    assert envfile.mask("supersecrettoken").startswith("sup")
    assert "secrettoken"[3:] not in envfile.mask("supersecrettoken")
    assert envfile.mask("") == ""
