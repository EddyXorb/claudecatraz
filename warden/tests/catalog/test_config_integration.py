"""Config-level integration (§04.2/04.3): from_env parses [api.endpoints],
Config.effective_endpoints builds (and memoizes) the table, and malformed
activation config surfaces as ConfigError just like any other bad
warden.toml.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from warden.core.config import Config, ConfigError
from warden.core.config_load import from_env
from warden.guards.gitlab_api.catalog.entries import DEFAULT_ENABLED

_MIN_ENV = {
    "ALLOWED_PROJECTS": "group/proj",
    "GITLAB_READ_TOKEN": "r",
    "GITLAB_WRITE_TOKEN": "w",
}


def test_bare_config_defaults_to_the_default_endpoint_set():
    cfg = Config(allowed_projects=("group/proj",), read_token="r", write_token="w")
    table = cfg.effective_endpoints
    assert {e.id for e in table.entries} == DEFAULT_ENABLED


def test_effective_endpoints_is_memoized():
    cfg = Config(allowed_projects=("group/proj",), read_token="r", write_token="w")
    assert cfg.effective_endpoints is cfg.effective_endpoints


def test_from_env_with_no_toml_file_uses_default_set(tmp_path: Path):
    cfg = from_env(_MIN_ENV, strict=True, toml_path=str(tmp_path / "nope.toml"))
    assert {e.id for e in cfg.effective_endpoints.entries} == DEFAULT_ENABLED


def test_from_env_parses_api_endpoints_enable(tmp_path: Path):
    toml = tmp_path / "warden.toml"
    toml.write_text(
        '[api.endpoints]\nenable = ["mr.create", "branch.create"]\n'
    )
    cfg = from_env(_MIN_ENV, strict=True, toml_path=str(toml))
    ids = {e.id for e in cfg.effective_endpoints.entries}
    assert ids == {"mr.create", "branch.create"}


def test_from_env_with_unknown_catalog_id_raises_config_error(tmp_path: Path):
    toml = tmp_path / "warden.toml"
    toml.write_text('[api.endpoints]\nenable = ["no.such.entry"]\n')
    with pytest.raises(ConfigError):
        # Malformed *shape* is caught by from_env itself; an unknown *id* is
        # only caught when the effective table is actually built (accessing
        # the cached_property), exactly like the startgate does at boot.
        from_env(_MIN_ENV, strict=True, toml_path=str(toml)).effective_endpoints


def test_from_env_with_malformed_enable_shape_raises_config_error_eagerly(tmp_path: Path):
    toml = tmp_path / "warden.toml"
    toml.write_text('[api.endpoints]\nenable = "not-a-list"\n')
    with pytest.raises(ConfigError):
        from_env(_MIN_ENV, strict=True, toml_path=str(toml))
