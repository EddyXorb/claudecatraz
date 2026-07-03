"""``[api.endpoints]`` TOML shape parsing (§04.2/04.3): config_parse.py stays
a thin shim over the generic decoder, free of any Catalog dependency, so
these tests exercise it purely against dicts shaped like what
``tomllib.load`` would hand back.
"""

from __future__ import annotations

import pytest

from warden.core.config import ConfigError
from warden.guards.gitlab_api.catalog.config_parse import ApiEndpointsConfig, parse_api_endpoints


def test_absent_file_yields_default_activation():
    assert parse_api_endpoints({}) == ApiEndpointsConfig()


def test_absent_endpoints_table_yields_default_activation():
    assert parse_api_endpoints({"api": {}}) == ApiEndpointsConfig()


def test_enable_list_is_parsed_as_a_tuple():
    act = parse_api_endpoints({"api": {"endpoints": {"enable": ["mr.create", "mr.note"]}}})
    assert act.enable == ("mr.create", "mr.note")


def test_explicit_empty_enable_list_is_distinguishable_from_absent():
    # An explicit `enable = []` must NOT collapse to "use the default set" —
    # only an absent section does that (§04.3 behaviour preservation).
    act = parse_api_endpoints({"api": {"endpoints": {"enable": []}}})
    assert act.enable == ()


@pytest.mark.parametrize(
    "file",
    [
        {"api": "not-a-table"},
        {"api": {"endpoints": "not-a-table"}},
        {"api": {"endpoints": {"enable": "not-a-list"}}},
        {"api": {"endpoints": {"enable": [1, 2]}}},  # not all strings
        {"api": {"endpoints": {"unknown_key": True}}},
    ],
)
def test_malformed_shapes_raise_config_error(file):
    with pytest.raises(ConfigError):
        parse_api_endpoints(file)
