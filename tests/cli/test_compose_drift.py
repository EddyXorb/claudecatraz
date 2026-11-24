"""Drift between running containers and the rendered compose config."""

import subprocess
from pathlib import Path
from typing import Any

import pytest

from catraz import compose
from catraz.paths import asset_root

ROWS = [
    {"Service": "gitlab-warden", "Name": "w"},
    {"Service": "forward-proxy", "Name": "p"},
    {"Service": "claude-dev-env", "Name": "a"},
]


class R:
    def __init__(self, rc: int, out: str = "") -> None:
        self.returncode, self.stdout = rc, out


def _fake(monkeypatch: pytest.MonkeyPatch, hashes: R, labels: R) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_compose_run(root: Path, args: list[str], **kw: Any) -> R:
        calls.append(args)
        return hashes

    def fake_subprocess_run(cmd: list[str], **kw: Any) -> R:
        calls.append(cmd)
        return labels

    monkeypatch.setattr(compose, "run", fake_compose_run)
    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    return calls


def test_drift_compares_infra_hashes_with_container_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake(
        monkeypatch,
        R(0, "gitlab-warden aaa\nforward-proxy bbb\n"),
        R(0, "aaa\nold\n"),
    )
    assert compose.drifted_services(tmp_path, ROWS, prefix=[]) == {"forward-proxy"}
    assert calls[0] == ["config", "--hash", "gitlab-warden,forward-proxy"]
    assert calls[1][-2:] == ["w", "p"]


def test_container_without_hash_label_is_drifted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake(monkeypatch, R(0, "gitlab-warden aaa\n"), R(0, "\n"))
    assert compose.drifted_services(tmp_path, ROWS[:1], prefix=[]) == {"gitlab-warden"}


@pytest.mark.parametrize("hash_rc,label_rc", [(1, 0), (0, 1)])
def test_unreadable_side_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hash_rc: int, label_rc: int
) -> None:
    _fake(monkeypatch, R(hash_rc, "gitlab-warden aaa\n"), R(label_rc, "aaa\n"))
    assert compose.drifted_services(tmp_path, ROWS[:1], prefix=[]) is None


def test_no_infra_running_is_no_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake(monkeypatch, R(1), R(1))
    assert compose.drifted_services(tmp_path, ROWS[2:], prefix=[]) == set()
    assert calls == []


def _endpoints(root: Path, *hosts: str) -> None:
    p = root / ".catraz/config/warden.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(f'[[git.endpoint]]\nhost = "{h}"\ntype = "gitlab"\n' for h in hosts))


@pytest.mark.parametrize(
    "no_proxy,stale",
    [
        ("a.example,localhost,127.0.0.1", False),
        ("localhost,127.0.0.1", True),
        ("a.example,b.example,localhost,127.0.0.1", True),
    ],
)
def test_agent_routing_stale_compares_no_proxy_with_endpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_proxy: str, stale: bool
) -> None:
    _endpoints(tmp_path, "a.example")
    env = f"PATH=/bin\nno_proxy={no_proxy}\nNO_PROXY={no_proxy}\n"
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: R(0, env))
    assert compose.agent_routing_stale(tmp_path, "a") is stale


def test_agent_routing_unreadable_is_not_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _endpoints(tmp_path, "a.example")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: R(1))
    assert compose.agent_routing_stale(tmp_path, "a") is False


def test_build_context_change_changes_compose_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An edited Dockerfile must reach the rendered config, or drift misses it."""
    for ctx in compose.BUILD_CONTEXTS.values():
        (tmp_path / "assets" / ctx).mkdir(parents=True)
        (tmp_path / "assets" / ctx / "Dockerfile").write_text("FROM a\n")
    monkeypatch.setattr(compose, "asset_root", lambda: tmp_path)
    before = compose._compose_env(tmp_path, None)
    (tmp_path / "assets/forward-proxy/Dockerfile").write_text("FROM b\n")
    after = compose._compose_env(tmp_path, {"X": "1"})
    assert before["CATRAZ_WARDEN_BUILD_HASH"] == after["CATRAZ_WARDEN_BUILD_HASH"]
    assert before["CATRAZ_PROXY_BUILD_HASH"] != after["CATRAZ_PROXY_BUILD_HASH"]
    assert after["X"] == "1"


def test_every_build_input_is_in_a_service_label() -> None:
    """Compose drops `build` from the config hash, so build args must ride along."""
    text = (asset_root() / "assets/compose/docker-compose.yml").read_text()
    assert text.count("DEV_UID: ${DEV_UID:-1000}") == len(compose.BUILD_CONTEXTS)
    for var in compose.BUILD_CONTEXTS:
        assert f"catraz.build-hash: ${{{var}:-}}-uid${{DEV_UID:-1000}}" in text
