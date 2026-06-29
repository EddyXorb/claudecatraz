import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path


def test_entrypoint_imports() -> None:
    p = Path(__file__).resolve().parents[2] / "src/catraz/assets/container/entrypoint.py"
    loader = SourceFileLoader("entrypoint", str(p))
    spec = importlib.util.spec_from_loader("entrypoint", loader)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec); loader.exec_module(mod)
    assert hasattr(mod, "cmd_start") and hasattr(mod, "cmd_sync")
