from pathlib import Path
from catraz.paths import asset_root


def test_layer_dockerfiles_present() -> None:
    ar = asset_root() / "assets"
    cl = (ar / "claude-layer/Dockerfile").read_text()
    assert "ARG BASE_IMAGE" in cl and "FROM ${BASE_IMAGE}" in cl


def test_default_image_dockerfile_present() -> None:
    ar = asset_root() / "assets"
    df = (ar / "image/Dockerfile").read_text()
    assert "FROM ubuntu:24.04" in df


def test_claude_layer_has_build_deps() -> None:
    """claude-layer must install git, curl, ca-certificates, gnupg unconditionally.

    These are runtime (git) and build-time (curl, gnupg for NodeSource) deps that
    must not be delegated to the user's swappable base image.
    """
    ar = asset_root() / "assets"
    cl = (ar / "claude-layer/Dockerfile").read_text()
    for dep in ("git", "curl", "ca-certificates", "gnupg"):
        assert dep in cl, f"claude-layer/Dockerfile missing required dep: {dep}"
