from pathlib import Path
from catraz.paths import asset_root


def test_layer_dockerfiles_present() -> None:
    ar = asset_root() / "assets"
    cl = (ar / "claude-layer/Dockerfile").read_text()
    assert "ARG BASE_IMAGE" in cl and "FROM ${BASE_IMAGE}" in cl
    assert (ar / "bases/cpp-rust-python/Dockerfile").exists()
