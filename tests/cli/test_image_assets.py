from catraz.paths import asset_root


def test_layer_dockerfiles_present() -> None:
    """§05.3: the claude layer now lives under assets/agents/claude/ (moved
    from assets/claude-layer/ in §06-migration.md Schritt 7)."""
    ar = asset_root() / "assets"
    cl = (ar / "agents/claude/layer.Dockerfile").read_text()
    assert "ARG BASE_IMAGE" in cl and "FROM ${BASE_IMAGE}" in cl


def test_default_image_dockerfile_present() -> None:
    ar = asset_root() / "assets"
    df = (ar / "image/Dockerfile").read_text()
    assert "FROM ubuntu:24.04" in df


def test_claude_layer_has_build_deps() -> None:
    """The claude layer must install git, curl, ca-certificates, gnupg unconditionally.

    These are runtime (git) and build-time (curl, gnupg for NodeSource) deps that
    must not be delegated to the user's swappable base image.
    """
    ar = asset_root() / "assets"
    cl = (ar / "agents/claude/layer.Dockerfile").read_text()
    for dep in ("git", "curl", "ca-certificates", "gnupg"):
        assert dep in cl, f"agents/claude/layer.Dockerfile missing required dep: {dep}"


def test_claude_agent_manifest_present() -> None:
    """§05.3: agent.toml carries the fields the CLI reads instead of constants."""
    ar = asset_root() / "assets"
    manifest = (ar / "agents/claude/agent.toml").read_text()
    for field in ('name', 'command', "subscription_source", "api_key_env",
                  "mode", "remote", "debug_flag", "domains"):
        assert field in manifest, f"agent.toml missing field: {field}"
