"""warden.core.toml_codec: the generic TOML-shaped-dict → dataclass decoder."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Optional

import pytest

from warden.core import toml_codec
from warden.core.config import ConfigError
from warden.guards.gitlab_api.catalog.config_parse import ApiEndpointsConfig


@dataclass(frozen=True)
class _Inner:
    name: str
    count: int = 3


@dataclass(frozen=True)
class _Outer:
    label: str
    enabled: bool
    ratio: float
    tags: tuple[str, ...]
    inner: _Inner
    nickname: Optional[str] = None


# --- happy path --------------------------------------------------------------


def test_decodes_primitives_tuple_optional_and_nested_dataclass():
    mapping = {
        "label": "x",
        "enabled": True,
        "ratio": 0.5,
        "tags": ["a", "b"],
        "inner": {"name": "n", "count": 7},
    }
    obj = toml_codec.decode(_Outer, mapping)
    assert obj == _Outer(
        label="x", enabled=True, ratio=0.5, tags=("a", "b"), inner=_Inner("n", 7), nickname=None
    )


def test_optional_field_present_is_decoded():
    mapping = {
        "label": "x",
        "enabled": False,
        "ratio": 1.0,
        "tags": [],
        "inner": {"name": "n"},
        "nickname": "nn",
    }
    obj = toml_codec.decode(_Outer, mapping)
    assert obj.nickname == "nn"
    assert obj.inner == _Inner("n")  # nested default applies


def test_missing_field_with_default_is_omitted():
    mapping = {
        "label": "x",
        "enabled": False,
        "ratio": 1.0,
        "tags": [],
        "inner": {"name": "n"},
    }
    obj = toml_codec.decode(_Outer, mapping)
    assert obj.nickname is None


# --- fail-closed ---------------------------------------------------------------


def test_unknown_key_raises_config_error():
    mapping = {
        "label": "x",
        "enabled": True,
        "ratio": 0.5,
        "tags": [],
        "inner": {"name": "n"},
        "bogus": 1,
    }
    with pytest.raises(ConfigError, match="unknown key"):
        toml_codec.decode(_Outer, mapping)


def test_missing_required_field_raises_config_error():
    mapping = {"label": "x", "enabled": True, "ratio": 0.5, "tags": []}
    with pytest.raises(ConfigError, match="missing required field"):
        toml_codec.decode(_Outer, mapping)


def test_type_mismatch_string_for_int_raises_config_error():
    with pytest.raises(ConfigError):
        toml_codec.decode(_Inner, {"name": "n", "count": "not-an-int"})


def test_bool_for_int_field_is_rejected():
    # isinstance(True, int) is True in Python — must be explicitly rejected.
    with pytest.raises(ConfigError, match="expected an int"):
        toml_codec.decode(_Inner, {"name": "n", "count": True})


def test_non_list_for_tuple_field_raises_config_error():
    mapping = {
        "label": "x",
        "enabled": True,
        "ratio": 0.5,
        "tags": "not-a-list",
        "inner": {"name": "n"},
    }
    with pytest.raises(ConfigError, match="expected a list"):
        toml_codec.decode(_Outer, mapping)


def test_non_table_for_nested_dataclass_raises_config_error():
    mapping = {
        "label": "x",
        "enabled": True,
        "ratio": 0.5,
        "tags": [],
        "inner": "not-a-table",
    }
    with pytest.raises(ConfigError):
        toml_codec.decode(_Outer, mapping)


def test_error_message_is_path_prefixed_for_nested_field():
    mapping = {
        "label": "x",
        "enabled": True,
        "ratio": 0.5,
        "tags": [],
        "inner": {"name": "n", "count": "nope"},
    }
    with pytest.raises(ConfigError, match=r"^inner\.count:"):
        toml_codec.decode(_Outer, mapping)


# --- round-trip: dataclass defaults -> minimal TOML mapping -> decode -------


def _minimal_mapping(instance: object) -> dict[str, object]:
    """The smallest TOML-shaped mapping that reproduces *instance*: a value
    equal to its field's own default is omitted (the decoder falls back to
    the dataclass default for any absent key), tuples render as lists."""
    mapping: dict[str, object] = {}
    for f in fields(instance):  # type: ignore[arg-type]
        value = getattr(instance, f.name)
        if value == f.default:
            continue
        mapping[f.name] = list(value) if isinstance(value, tuple) else value
    return mapping


@pytest.mark.parametrize(
    "instance", [ApiEndpointsConfig(), ApiEndpointsConfig(enable=("mr.create", "mr.note"))]
)
def test_dataclass_instance_round_trips_through_the_decoder(instance):
    mapping = _minimal_mapping(instance)
    decoded = toml_codec.decode(type(instance), mapping)
    assert decoded == instance
