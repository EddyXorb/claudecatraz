"""`_run_sync` calls the resolved profile's adapter in-process — no
subprocess/entrypoint.py indirection (host-side sync never needs a
container). See tests/container/test_sync.py for the adapter-level
`sync_from_host` behavior."""

from pathlib import Path
from typing import Any
import pytest
from catraz import cli
from catraz.commands.setup import _sync as setup_sync
from catraz.errors import CliError


def _seed(tmp_path: Path) -> None:
    (tmp_path / ".catraz").mkdir()
    (tmp_path / ".catraz/.env").write_text("AUTH_MODE=subscription\n")


@pytest.fixture(autouse=True)
def _sync_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup_sync, "_credentials_mode", lambda root: "sync")


class _FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, Any]] = []

    def sync_from_host(self, source: Any, home: Any) -> None:
        self.calls.append((source, home))


def test_run_sync_calls_adapter_in_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(tmp_path)
    fake = _FakeAdapter()
    monkeypatch.setattr(setup_sync, "load_adapter_module", lambda profile: fake)
    cli._run_sync(tmp_path, cli.Out(color=False))
    assert len(fake.calls) == 1
    _, home = fake.calls[0]
    assert str(home).endswith("secrets/claude")


def test_run_sync_raises_when_adapter_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path)

    def boom(profile: str) -> Any:
        raise CliError("adapter not found (corrupt cache?)", 1)

    monkeypatch.setattr(setup_sync, "load_adapter_module", boom)
    with pytest.raises(CliError):
        cli._run_sync(tmp_path, cli.Out(color=False))


def test_run_sync_propagates_adapter_failure_as_clierror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(tmp_path)

    class _FailingAdapter:
        def sync_from_host(self, source: Any, home: Any) -> None:
            import sys

            sys.exit("error: no host credential found")

    monkeypatch.setattr(setup_sync, "load_adapter_module", lambda profile: _FailingAdapter())
    with pytest.raises(CliError) as ei:
        cli._run_sync(tmp_path, cli.Out(color=False))
    assert "no host credential found" in str(ei.value)
