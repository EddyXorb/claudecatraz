import re
from pathlib import Path
import pytest
from catraz import compose


def test_base_cmd_points_at_asset_and_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".catraz").mkdir()
    cmd = compose.base_cmd(tmp_path)
    assert "--project-directory" in cmd and str(tmp_path) in cmd
    assert cmd[cmd.index("-f") + 1].endswith("assets/compose/docker-compose.yml")


def test_base_cmd_sets_unique_project_name(tmp_path: Path) -> None:
    cmd = compose.base_cmd(tmp_path)
    assert "--project-name" in cmd
    assert cmd[cmd.index("--project-name") + 1] == compose.project_name(tmp_path)


def test_project_name_is_valid_stable_and_unique(tmp_path: Path) -> None:
    a = tmp_path / "work" / "api"
    b = tmp_path / "scratch" / "api"  # same basename, different path
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    na, nb = compose.project_name(a), compose.project_name(b)
    # valid Compose project name
    assert re.fullmatch(r"[a-z0-9][a-z0-9_-]*", na)
    # same basename but DIFFERENT names → no cross-sandbox collision
    assert na != nb
    assert na.startswith("catraz-api-")
    # stable for the same path
    assert compose.project_name(a) == na


def test_project_name_handles_exotic_basename(tmp_path: Path) -> None:
    d = tmp_path / "My Project!!"
    d.mkdir()
    assert re.fullmatch(r"[a-z0-9][a-z0-9_-]*", compose.project_name(d))


def test_base_cmd_includes_override_when_present(tmp_path: Path) -> None:
    (tmp_path / ".catraz").mkdir()
    (tmp_path / ".catraz/compose.override.yml").write_text("services: {}\n")
    assert str(tmp_path / ".catraz/compose.override.yml") in compose.base_cmd(tmp_path)


# ── per-host DNS-alias/no_proxy compose fragment ──────────────────────


def _write_endpoints(tmp_path: Path, hosts: list[str]) -> None:
    config_dir = tmp_path / ".catraz" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    lines = ["[git.rules]", ""]
    for host in hosts:
        lines += ["[[git.endpoint]]", f'host = "{host}"', 'type = "gitlab"', ""]
    (config_dir / "warden.toml").write_text("\n".join(lines))


def test_git_endpoint_hosts_reads_warden_toml(tmp_path: Path) -> None:
    _write_endpoints(tmp_path, ["gitlab.com", "my-gitlab.de"])
    assert compose._git_endpoint_hosts(tmp_path) == ["gitlab.com", "my-gitlab.de"]


def test_git_endpoint_hosts_missing_file_is_empty(tmp_path: Path) -> None:
    assert compose._git_endpoint_hosts(tmp_path) == []


def test_git_endpoint_hosts_malformed_toml_is_empty(tmp_path: Path) -> None:
    config_dir = tmp_path / ".catraz" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "warden.toml").write_text("not [ valid toml")
    assert compose._git_endpoint_hosts(tmp_path) == []


def test_render_hosts_fragment_lists_every_host_as_alias(tmp_path: Path) -> None:
    text = compose.render_hosts_fragment(["gitlab.com", "my-gitlab.de"])
    assert "- gitlab.com" in text
    assert "- my-gitlab.de" in text
    assert "agent-net" in text
    # The compose *service* is necessarily named gitlab-warden (needs a key
    # for the alias) — no-leak checks are about the agent's own remotes, not this.


def test_render_hosts_fragment_no_proxy_includes_every_host_plus_loopback(
    tmp_path: Path,
) -> None:
    text = compose.render_hosts_fragment(["gitlab.com", "my-gitlab.de"])
    assert "no_proxy=gitlab.com,my-gitlab.de,localhost,127.0.0.1" in text
    assert "NO_PROXY=gitlab.com,my-gitlab.de,localhost,127.0.0.1" in text


def test_render_hosts_fragment_empty_hosts_is_valid_shape(tmp_path: Path) -> None:
    text = compose.render_hosts_fragment([])
    assert "aliases: []" in text
    assert "no_proxy=localhost,127.0.0.1" in text


def test_write_hosts_fragment_writes_from_warden_toml(tmp_path: Path) -> None:
    (tmp_path / ".catraz").mkdir()
    _write_endpoints(tmp_path, ["gitlab.com"])
    path = compose.write_hosts_fragment(tmp_path)
    assert path == tmp_path / ".catraz" / "compose.hosts.yml"
    assert "- gitlab.com" in path.read_text()


def test_source_cmd_includes_hosts_fragment_when_present(tmp_path: Path) -> None:
    (tmp_path / ".catraz").mkdir()
    (tmp_path / ".catraz/compose.hosts.yml").write_text("services: {}\n")
    assert str(tmp_path / ".catraz/compose.hosts.yml") in compose.base_cmd(tmp_path)


def test_source_cmd_omits_hosts_fragment_when_absent(tmp_path: Path) -> None:
    (tmp_path / ".catraz").mkdir()
    cmd = compose.base_cmd(tmp_path)
    assert str(tmp_path / ".catraz/compose.hosts.yml") not in cmd


def test_source_cmd_orders_hosts_fragment_before_user_override(tmp_path: Path) -> None:
    """The user's own compose.override.yml must be able to win over the
    generated fragment (last -f layer wins on a merge conflict)."""
    (tmp_path / ".catraz").mkdir()
    (tmp_path / ".catraz/compose.hosts.yml").write_text("services: {}\n")
    (tmp_path / ".catraz/compose.override.yml").write_text("services: {}\n")
    cmd = compose.base_cmd(tmp_path)
    hosts_idx = cmd.index(str(tmp_path / ".catraz/compose.hosts.yml"))
    override_idx = cmd.index(str(tmp_path / ".catraz/compose.override.yml"))
    assert hosts_idx < override_idx


# ── credentials.mode overlay selection ────────────────────────────────


def _persistent_home_yml() -> str:
    from catraz.paths import asset_root

    return str(asset_root() / "assets/compose/home.persistent.yml")


def test_persistent_mode_layers_home_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".catraz").mkdir()
    monkeypatch.setattr(compose, "_credentials_mode", lambda root: "persistent")
    cmd = compose.base_cmd(tmp_path)
    assert _persistent_home_yml() in cmd
    # After the auth layer, before any user override (override still wins last).
    auth_idx = next(i for i, a in enumerate(cmd) if a.endswith("auth.subscription.yml"))
    assert cmd.index(_persistent_home_yml()) > auth_idx


def test_sync_mode_omits_home_overlay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".catraz").mkdir()
    monkeypatch.setattr(compose, "_credentials_mode", lambda root: "sync")
    assert _persistent_home_yml() not in compose.base_cmd(tmp_path)


def test_claude_default_profile_is_persistent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shipped claude manifest selects persistent, so a default setup gets
    the full-bind home overlay (and run/shell inherit it via _source_cmd)."""
    (tmp_path / ".catraz").mkdir()
    (tmp_path / ".catraz" / ".env").write_text("AUTH_MODE=subscription\n")
    assert compose._credentials_mode(tmp_path) == "persistent"
    assert _persistent_home_yml() in compose.base_cmd(tmp_path)


# ── CLAUDE_CREDENTIALS_MODE env override ────────────────────────────────


def test_credentials_mode_env_override_to_sync(tmp_path: Path) -> None:
    """CLAUDE_CREDENTIALS_MODE=sync overrides the claude manifest's persistent
    default, independently of the manifest."""
    (tmp_path / ".catraz").mkdir()
    (tmp_path / ".catraz" / ".env").write_text(
        "AUTH_MODE=subscription\nCLAUDE_CREDENTIALS_MODE=sync\n"
    )
    assert compose._credentials_mode(tmp_path) == "sync"
    assert _persistent_home_yml() not in compose.base_cmd(tmp_path)


def test_credentials_mode_env_override_to_persistent_wins_over_sync_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLAUDE_CREDENTIALS_MODE=persistent overrides a manifest default of sync."""
    import catraz.agents as agents_mod

    (tmp_path / ".catraz").mkdir()
    (tmp_path / ".catraz" / ".env").write_text(
        "AUTH_MODE=subscription\nCLAUDE_CREDENTIALS_MODE=persistent\n"
    )
    fake = agents_mod.AgentManifest(
        name="claude",
        command="claude",
        subscription_source="",
        api_key_env="ANTHROPIC_API_KEY",
        credentials_mode="sync",
        remote_allowed=True,
        debug_flag="--debug-file",
        egress_domains=(),
    )
    monkeypatch.setattr(agents_mod, "load_manifest", lambda profile: fake)
    assert compose._credentials_mode(tmp_path) == "persistent"
    assert _persistent_home_yml() in compose.base_cmd(tmp_path)


def test_credentials_mode_invalid_env_value_falls_back_to_manifest(tmp_path: Path) -> None:
    """A garbage CLAUDE_CREDENTIALS_MODE value never silently flips the mode —
    it falls through to the manifest default."""
    (tmp_path / ".catraz").mkdir()
    (tmp_path / ".catraz" / ".env").write_text(
        "AUTH_MODE=subscription\nCLAUDE_CREDENTIALS_MODE=bogus\n"
    )
    assert compose._credentials_mode(tmp_path) == "persistent"
