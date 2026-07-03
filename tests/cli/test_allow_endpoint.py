"""catraz allow-endpoint (§04.2/04.3), in the style of tests/cli/test_allow.py."""

from __future__ import annotations

import argparse
import types
from pathlib import Path
from typing import cast

import pytest

from catraz.admin_client import AdminUnreachable
from catraz.commands import endpoints as endpoints_cmd
from catraz.endpoints import read_enable_list
from catraz.errors import EXIT_CONFIG, EXIT_OK, CliError
from catraz.ui import Out


def _out() -> Out:
    return Out(color=False)


def _ns(endpoint_ids: list[str]) -> types.SimpleNamespace:
    return types.SimpleNamespace(endpoint_ids=endpoint_ids)


def _seed(tmp_path: Path, extra: str = "") -> Path:
    cfg = tmp_path / ".catraz" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "warden.toml").write_text(f"allowed_projects = []\n{extra}")
    return cfg / "warden.toml"


_DEFAULT_CATALOG_REPORT = {
    "catalog": [
        {"id": "mr.create", "active": True, "enabled_via": "default"},
        {"id": "mr.note", "active": True, "enabled_via": "default"},
        {"id": "branch.create", "active": False, "enabled_via": None},
        {"id": "issue.create", "active": False, "enabled_via": None},
    ],
}


# ── argparse wiring ──────────────────────────────────────────────────────────


def test_allow_endpoint_command_token() -> None:
    from catraz.cli import build_parser

    args = build_parser().parse_args(["allow-endpoint", "branch.create"])
    assert args.command == "allow-endpoint"
    assert args.endpoint_ids == ["branch.create"]


def test_allow_endpoint_accepts_multiple_ids() -> None:
    from catraz.cli import build_parser

    args = build_parser().parse_args(["allow-endpoint", "branch.create", "issue.create"])
    assert args.endpoint_ids == ["branch.create", "issue.create"]


def test_allow_endpoint_routes_to_its_handler() -> None:
    from catraz import cli

    assert cli.HANDLERS["allow-endpoint"] is endpoints_cmd.cmd_allow_endpoint


def test_not_set_up_raises(tmp_path: Path) -> None:
    with pytest.raises(CliError):
        endpoints_cmd.cmd_allow_endpoint(
            tmp_path, cast(argparse.Namespace, _ns(["mr.create"])), _out()
        )


def test_all_malformed_ids_is_nothing_to_enable(tmp_path: Path) -> None:
    _seed(tmp_path)
    rc = endpoints_cmd.cmd_allow_endpoint(
        tmp_path, cast(argparse.Namespace, _ns(["not valid!"])), _out()
    )
    assert rc == EXIT_CONFIG


# ── live (reachable admin socket) path ──────────────────────────────────────


def test_live_unknown_catalog_id_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    warden = _seed(tmp_path)
    monkeypatch.setattr(endpoints_cmd, "fetch_policy_report", lambda root: _DEFAULT_CATALOG_REPORT)
    rc = endpoints_cmd.cmd_allow_endpoint(
        tmp_path, cast(argparse.Namespace, _ns(["no.such.entry"])), _out()
    )
    assert rc == EXIT_CONFIG
    assert read_enable_list(warden) is None  # nothing written


def test_live_activates_a_known_extra_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    warden = _seed(tmp_path)
    monkeypatch.setattr(endpoints_cmd, "fetch_policy_report", lambda root: _DEFAULT_CATALOG_REPORT)
    rc = endpoints_cmd.cmd_allow_endpoint(
        tmp_path, cast(argparse.Namespace, _ns(["branch.create"])), _out()
    )
    assert rc == EXIT_OK
    assert read_enable_list(warden) == ["mr.create", "mr.note", "branch.create"]


def test_live_idempotent_when_already_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path)
    monkeypatch.setattr(endpoints_cmd, "fetch_policy_report", lambda root: _DEFAULT_CATALOG_REPORT)
    rc = endpoints_cmd.cmd_allow_endpoint(
        tmp_path, cast(argparse.Namespace, _ns(["mr.create"])), _out()
    )
    assert rc == EXIT_OK


# ── offline (admin socket unreachable) path ─────────────────────────────────


def _unreachable(root: Path) -> dict[str, object]:
    raise AdminUnreachable("admin socket not found")


def test_offline_with_existing_section_merges_without_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    warden = _seed(tmp_path, extra='\n[api.endpoints]\nenable = ["mr.create"]\n')
    monkeypatch.setattr(endpoints_cmd, "fetch_policy_report", _unreachable)
    rc = endpoints_cmd.cmd_allow_endpoint(
        tmp_path, cast(argparse.Namespace, _ns(["branch.create"])), _out()
    )
    assert rc == EXIT_OK
    assert read_enable_list(warden) == ["mr.create", "branch.create"]


def test_offline_without_existing_section_refuses_to_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    warden = _seed(tmp_path)
    monkeypatch.setattr(endpoints_cmd, "fetch_policy_report", _unreachable)
    rc = endpoints_cmd.cmd_allow_endpoint(
        tmp_path, cast(argparse.Namespace, _ns(["branch.create"])), _out()
    )
    assert rc == EXIT_CONFIG
    assert read_enable_list(warden) is None  # nothing written
    out = capsys.readouterr().out
    assert "[api.endpoints]" in out
    assert "branch.create" in out
