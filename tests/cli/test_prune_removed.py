"""Regression: catraz prune was removed (P9)."""
import pytest
import catraz.image
from catraz.cli import build_parser, HANDLERS


def test_prune_not_in_handlers() -> None:
    assert "prune" not in HANDLERS


def test_prune_parser_rejects() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["prune"])


def test_image_has_no_prune() -> None:
    assert not hasattr(catraz.image, "prune")
