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
from warden.guards.gitlab_api.catalog import StartgateFailure


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


def test_main_exits_2_on_startgate_failure(monkeypatch):
    # §04.4: a catalog deny-probe that would be ALLOWED is a fail-closed
    # startup abort, exactly like a bad config or an unrecognised schema.
    monkeypatch.setattr(main_mod, "from_env", lambda: Config())

    def _raise(cfg, table) -> None:
        raise StartgateFailure("probe 'x' was ALLOWED")

    monkeypatch.setattr(main_mod, "run_startgate", _raise)
    with pytest.raises(SystemExit) as exc:
        main_mod.main()
    assert exc.value.code == 2
