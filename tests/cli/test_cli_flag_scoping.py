"""Tests for CLI flag scoping.

All assertions go through build_parser().parse_args() — NOT SimpleNamespace
hand-builds — so that argparse scoping regressions are actually caught."""

import argparse
import pytest

from catraz.cli import build_parser


def parse(argv: list[str]) -> argparse.Namespace:
    """Return parsed Namespace; let argparse raise SystemExit on error."""
    return build_parser().parse_args(argv)


# ── 1. Post-form stays valid ──────────────────────────────────────────────────


def test_down_print_post() -> None:
    args = parse(["down", "--print"])
    assert args.print_only is True


def test_down_dry_run_post() -> None:
    args = parse(["down", "--dry-run"])
    assert args.print_only is True


def test_init_yes_short_post() -> None:
    args = parse(["init", "-y"])
    assert args.yes is True


def test_init_yes_long_post() -> None:
    args = parse(["init", "--yes"])
    assert args.yes is True


# ── 2. Wrong-command fail-loud ────────────────────────────────────────────────


def test_status_dry_run_fails(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        parse(["status", "--dry-run"])


def test_logs_yes_fails(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        parse(["logs", "--yes"])


def test_audit_dry_run_fails(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        parse(["audit", "--dry-run"])


# ── 3. Pre-subcommand form now fail-loud (pins deliberate decision) ───────────


def test_dry_run_before_stop_fails(capsys: pytest.CaptureFixture[str]) -> None:
    """--dry-run is no longer on the top-level parser; pre-subcommand form must fail."""
    with pytest.raises(SystemExit):
        parse(["--dry-run", "stop"])


def test_yes_before_init_fails(capsys: pytest.CaptureFixture[str]) -> None:
    """-y is no longer on the top-level parser; pre-subcommand form must fail."""
    with pytest.raises(SystemExit):
        parse(["-y", "init"])


# ── 4. Truly-global flags still work pre AND post ─────────────────────────────


def test_dir_pre_subcommand() -> None:
    args = parse(["-C", "/x", "status"])
    assert getattr(args, "dir", None) == "/x"


def test_dir_post_subcommand() -> None:
    args = parse(["status", "-C", "/x"])
    assert getattr(args, "dir", None) == "/x"


def test_no_color_pre_subcommand() -> None:
    args = parse(["--no-color", "status"])
    # SUPPRESS default means attribute may or may not exist, but if set it's True
    assert getattr(args, "no_color", False) is True


def test_no_color_post_subcommand() -> None:
    args = parse(["status", "--no-color"])
    assert getattr(args, "no_color", False) is True


# ── 5. Default mechanic ───────────────────────────────────────────────────────


def test_print_only_default_for_status() -> None:
    """Commands that don't declare --dry-run must not have print_only set."""
    args = parse(["status"])
    assert getattr(args, "print_only", False) is False


def test_print_only_default_for_doctor() -> None:
    args = parse(["doctor"])
    assert getattr(args, "print_only", False) is False


def test_yes_default_for_doctor() -> None:
    args = parse(["doctor"])
    assert getattr(args, "yes", False) is False


def test_yes_default_for_status() -> None:
    args = parse(["status"])
    assert getattr(args, "yes", False) is False
