"""__main__.py: fail-closed startup — a config or schema error exits 2, not a
traceback.

The uvicorn serve loop itself is orchestration and not unit-tested; what matters
for the container is that an invalid configuration or an unrecognised state DB
schema version aborts cleanly with code 2 (A9).
"""

from __future__ import annotations

import logging

import pytest

import warden.__main__ as main_mod
from warden.core.config import Config, ConfigError, GitEndpoint
from warden.core.logging_setup import configure_logging
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


def test_main_exits_2_on_catalog_config_error(monkeypatch, tmp_path):
    # build_context() (called at the __main__ composition root) constructs
    # ApiGuard, whose __init__ calls build_effective_table (once per configured
    # host) and raises CatalogConfigError fail-closed on an unknown action id —
    # this must abort exactly like any other fail-closed startup error, not
    # surface as a raw traceback. A hand-built Config bypasses the loader's own
    # vocabulary validation (core.config_load._parse_actions), same as a
    # malformed [[git.endpoint]] actions override would if that check were ever
    # skipped. state_db_path/audit_log_path are set to in-memory/stdout,
    # log_path to a tmp file, so this exercises the real construction path
    # without touching disk outside the test's tmp dir.
    monkeypatch.setattr(
        main_mod,
        "from_env",
        lambda: Config(
            git_endpoints=(
                GitEndpoint(host="gitlab.example", type="gitlab", actions=("no.such.entry",)),
            ),
            state_db_path=":memory:",
            audit_log_path="-",
            log_path=str(tmp_path / "warden.log"),
        ),
    )
    with pytest.raises(SystemExit) as exc:
        main_mod.main()
    assert exc.value.code == 2


def test_configure_logging_writes_to_file(tmp_path):
    log_path = tmp_path / "warden.log"
    configure_logging(str(log_path))
    logging.getLogger("warden").warning("hello from test")

    assert log_path.exists()
    assert "hello from test" in log_path.read_text()


def test_configure_logging_is_idempotent(tmp_path):
    log_path = tmp_path / "warden.log"
    configure_logging(str(log_path))
    configure_logging(str(log_path))

    root = logging.getLogger()
    file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
    stream_handlers = [
        h
        for h in root.handlers
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
    ]
    assert len(file_handlers) == 1
    assert len(stream_handlers) == 1
