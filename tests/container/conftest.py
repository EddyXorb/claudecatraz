import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest


@pytest.fixture
def ep():
    """The container entrypoint module, loaded by path (it is an asset, not an import)."""
    p = Path(__file__).resolve().parents[2] / "src/catraz/assets/container/entrypoint.py"
    loader = SourceFileLoader("entrypoint", str(p))
    spec = importlib.util.spec_from_loader("entrypoint", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod
