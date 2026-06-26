"""__main__.py: fail-closed startup — a config error exits 2, not a traceback.

The uvicorn serve loop itself is orchestration and not unit-tested; what matters
for the container is that an invalid configuration aborts cleanly with code 2.
"""

from __future__ import annotations

import pytest

import warden.__main__ as main_mod
from warden.config import ConfigError


def test_main_exits_2_on_config_error(monkeypatch):
    def _raise() -> None:
        raise ConfigError("ALLOWED_PROJECTS must be non-empty (fail-closed)")

    monkeypatch.setattr(main_mod, "from_env", _raise)
    with pytest.raises(SystemExit) as exc:
        main_mod.main()
    assert exc.value.code == 2
