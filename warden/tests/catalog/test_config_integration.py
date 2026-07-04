"""Config-level integration (§04.2/04.3): from_env parses [api.endpoints]
into ``Config.endpoint_enable``, the gitlab_api guard builds the effective
table from it, and malformed activation config surfaces as ConfigError just
like any other bad warden.toml.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from warden.core.config import Config, ConfigError
from warden.core.config_load import from_env
from warden.guards.gitlab_api.catalog.activation import build_effective_table
from warden.guards.gitlab_api.catalog.errors import CatalogConfigError
from warden.guards.gitlab_api.catalog.write_endpoints import DEFAULT_ENABLED

# Not a "minimal valid config" anymore (step 05: from_env has no fail-stop
# token/allowlist requirement left) — an empty env is just as strict-valid.
# Kept as a named constant since these tests are about [api.endpoints], not
# about what env from_env accepts.
_MIN_ENV: dict[str, str] = {}


def test_bare_config_defaults_to_the_default_endpoint_set():
    cfg = Config(allowed_projects=("group/proj",))
    table = build_effective_table(cfg, cfg.endpoint_enable)
    assert {e.id for e in table.entries} == DEFAULT_ENABLED


def test_from_env_with_no_toml_file_uses_default_set(tmp_path: Path):
    cfg = from_env(_MIN_ENV, strict=True, toml_path=str(tmp_path / "nope.toml"))
    assert cfg.endpoint_enable is None
    table = build_effective_table(cfg, cfg.endpoint_enable)
    assert {e.id for e in table.entries} == DEFAULT_ENABLED


def test_from_env_parses_api_endpoints_enable(tmp_path: Path):
    toml = tmp_path / "warden.toml"
    toml.write_text('[api.endpoints]\nenable = ["mr.create", "branch.create"]\n')
    cfg = from_env(_MIN_ENV, strict=True, toml_path=str(toml))
    assert cfg.endpoint_enable == ("mr.create", "branch.create")
    table = build_effective_table(cfg, cfg.endpoint_enable)
    assert {e.id for e in table.entries} == {"mr.create", "branch.create"}


def test_from_env_with_unknown_catalog_id_is_only_caught_when_the_table_is_built(
    tmp_path: Path,
):
    toml = tmp_path / "warden.toml"
    toml.write_text('[api.endpoints]\nenable = ["no.such.entry"]\n')
    # Malformed *shape* is caught by from_env itself (below); an unknown *id*
    # is only caught when the effective table is actually built — the same
    # deferred check ApiGuard.__init__ relies on at boot.
    cfg = from_env(_MIN_ENV, strict=True, toml_path=str(toml))
    with pytest.raises(CatalogConfigError):
        build_effective_table(cfg, cfg.endpoint_enable)


def test_from_env_with_malformed_enable_shape_raises_config_error_eagerly(tmp_path: Path):
    toml = tmp_path / "warden.toml"
    toml.write_text('[api.endpoints]\nenable = "not-a-list"\n')
    with pytest.raises(ConfigError):
        from_env(_MIN_ENV, strict=True, toml_path=str(toml))
