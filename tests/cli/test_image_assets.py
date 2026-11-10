from catraz.paths import asset_root


def test_layer_dockerfiles_present() -> None:
    """The claude layer lives under assets/agents/claude/."""
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
    """agent.toml carries the fields the CLI reads instead of constants."""
    ar = asset_root() / "assets"
    manifest = (ar / "agents/claude/agent.toml").read_text()
    for field in (
        "name",
        "command",
        "subscription_source",
        "api_key_env",
        "mode",
        "remote",
        "debug_flag",
        "domains",
    ):
        assert field in manifest, f"agent.toml missing field: {field}"


def test_claude_version_arg_only_busts_the_npm_layer() -> None:
    """CLAUDE_CODE_VERSION is declared after the apt/NodeSource RUN, so a version
    bump keeps that toolchain layer cached."""
    ar = asset_root() / "assets"
    lines = (ar / "agents/claude/layer.Dockerfile").read_text().splitlines()
    arg = lines.index("ARG CLAUDE_CODE_VERSION=latest")
    apt = next(i for i, ln in enumerate(lines) if ln.startswith("RUN apt-get update"))
    assert apt < arg
    assert lines[arg + 1].startswith("RUN npm install -g @anthropic-ai/claude-code@")
