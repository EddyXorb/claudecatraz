"""catraz.endpoints (§04.2/04.3): shape validation + [api.endpoints] TOML
read/write, in the style of tests/cli/test_allow.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from catraz.endpoints import (
    merge_endpoint_ids,
    read_enable_list,
    render_enable_block,
    validate_endpoint_id_shape,
    write_enable_list,
)


# ── validate_endpoint_id_shape ────────────────────────────────────────────────


@pytest.mark.parametrize("good", ["mr.create", "branch.create", "a.b", "mr.discussion_reply"])
def test_valid_shapes_pass(good: str) -> None:
    assert validate_endpoint_id_shape(good) is None


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "  ",
        "nodot",
        "Mr.Create",
        "mr..create",
        "mr.create!",
        "-mr.create",
        "mr.create.",
    ],
)
def test_invalid_shapes_are_rejected(bad: str) -> None:
    assert validate_endpoint_id_shape(bad) is not None


# ── merge_endpoint_ids ────────────────────────────────────────────────────────


def test_merge_appends_new_ids() -> None:
    assert merge_endpoint_ids(["mr.create"], ["branch.create"]) == [
        "mr.create",
        "branch.create",
    ]


def test_merge_dedupes_preserving_order() -> None:
    assert merge_endpoint_ids(["a.b"], ["a.b", "c.d", "c.d"]) == ["a.b", "c.d"]


def test_merge_empty_existing() -> None:
    assert merge_endpoint_ids([], ["mr.create"]) == ["mr.create"]


# ── read_enable_list ──────────────────────────────────────────────────────────


def test_read_enable_list_absent_section_is_none(tmp_path: Path) -> None:
    p = tmp_path / "warden.toml"
    p.write_text("allowed_projects = []\n")
    assert read_enable_list(p) is None


def test_read_enable_list_explicit_empty_is_distinguishable(tmp_path: Path) -> None:
    p = tmp_path / "warden.toml"
    p.write_text("[api.endpoints]\nenable = []\n")
    assert read_enable_list(p) == []


def test_read_enable_list_parses_ids(tmp_path: Path) -> None:
    p = tmp_path / "warden.toml"
    p.write_text('[api.endpoints]\nenable = ["mr.create", "branch.create"]\n')
    assert read_enable_list(p) == ["mr.create", "branch.create"]


def test_read_enable_list_malformed_toml_is_none(tmp_path: Path) -> None:
    p = tmp_path / "warden.toml"
    p.write_text("this is not [ valid toml")
    assert read_enable_list(p) is None


# ── render_enable_block ───────────────────────────────────────────────────────


def test_render_enable_block_shape() -> None:
    block = render_enable_block(["mr.create", "branch.create"])
    assert block == '[api.endpoints]\nenable = ["mr.create", "branch.create"]\n'


# ── write_enable_list ─────────────────────────────────────────────────────────


def test_write_enable_list_appends_absent_section(tmp_path: Path) -> None:
    p = tmp_path / "warden.toml"
    p.write_text("allowed_projects = []\n")
    write_enable_list(p, ["mr.create"])
    assert read_enable_list(p) == ["mr.create"]
    assert "allowed_projects" in p.read_text()  # existing content preserved


def test_write_enable_list_replaces_existing_in_place(tmp_path: Path) -> None:
    p = tmp_path / "warden.toml"
    p.write_text('[api.endpoints]\nenable = ["mr.create"]\n')
    write_enable_list(p, ["mr.create", "branch.create"])
    assert read_enable_list(p) == ["mr.create", "branch.create"]


def test_write_enable_list_refuses_section_without_enable_key(tmp_path: Path) -> None:
    p = tmp_path / "warden.toml"
    p.write_text('[api.endpoints.overrides."branch.create"]\nbranch_prefix = "claude/x-"\n')
    with pytest.raises(ValueError, match="enable"):
        write_enable_list(p, ["branch.create"])
