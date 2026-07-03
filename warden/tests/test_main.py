"""__main__.py: fail-closed startup — a config or schema error exits 2, not a
traceback.

The uvicorn serve loop itself is orchestration and not unit-tested; what matters
for the container is that an invalid configuration or an unrecognised state DB
schema version aborts cleanly with code 2 (A9).
"""

from __future__ import annotations

import pytest

import warden.__main__ as main_mod
from warden.core.config import Config, ConfigError
from warden.core.state import SchemaError


def test_main_exits_2_on_config_error(monkeypatch):
    def _raise() -> None:
        raise ConfigError("GITLAB_READ_TOKEN is required")

    monkeypatch.setattr(main_mod, "from_env", _raise)
    with pytest.raises(SystemExit) as exc:
        main_mod.main()
    assert exc.value.code == 2


def test_main_exits_2_on_schema_error(monkeypatch):
    def _raise() -> None:
        raise SchemaError("state DB schema version 99 is newer than this warden build supports")

    monkeypatch.setattr(main_mod, "from_env", _raise)
    with pytest.raises(SystemExit) as exc:
        main_mod.main()
    assert exc.value.code == 2


def test_main_exits_2_on_catalog_config_error(monkeypatch):
    # build_context() (called at the __main__ composition root) constructs
    # ApiGuard, whose __init__ calls build_effective_table and raises
    # CatalogConfigError fail-closed on an unknown [api.endpoints].enable id —
    # this must abort exactly like any other fail-closed startup error, not
    # surface as a raw traceback. state_db_path/audit_log_path are set to
    # in-memory/stdout so this exercises the real construction path without
    # touching disk.
    monkeypatch.setattr(
        main_mod,
        "from_env",
        lambda: Config(
            endpoint_enable=("no.such.entry",),
            state_db_path=":memory:",
            audit_log_path="-",
        ),
    )
    with pytest.raises(SystemExit) as exc:
        main_mod.main()
    assert exc.value.code == 2
