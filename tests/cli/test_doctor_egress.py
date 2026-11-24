from pathlib import Path

from catraz import doctor
from catraz.egress_allowlist import upsert_agent_block
from catraz.paths import asset_root


def _shipped_allowlist() -> str:
    return (asset_root() / "assets" / "config" / "allowlist.txt").read_text(encoding="utf-8")


def _baseline_entry_count(text: str) -> int:
    return sum(1 for ln in text.split("\n") if ln.strip() and not ln.strip().startswith("#"))


def _make_root(tmp_path: Path, allowlist_text: str | None) -> Path:
    root = tmp_path / "proj"
    (root / ".catraz" / "config").mkdir(parents=True)
    if allowlist_text is not None:
        (root / ".catraz" / "config" / "allowlist.txt").write_text(allowlist_text, encoding="utf-8")
    return root


def _egress_msgs(f: doctor.Findings) -> list[tuple[str, str]]:
    return [(lvl, msg) for lvl, sec, msg, _ in f.items if sec == "egress"]


def test_fresh_allowlist_reports_every_domain_as_baseline(tmp_path: Path) -> None:
    shipped = _shipped_allowlist()
    root = _make_root(tmp_path, shipped)
    f = doctor.run_doctor(root, only=["egress"])
    msgs = _egress_msgs(f)
    assert msgs, "egress section must report the allowed domains"
    assert all(lvl == doctor.OK for lvl, _ in msgs)
    assert all("[baseline]" in msg for _, msg in msgs)
    assert len(msgs) == _baseline_entry_count(shipped)


def test_agent_block_and_manual_line_get_their_provenance(tmp_path: Path) -> None:
    shipped = _shipped_allowlist()
    with_agent = upsert_agent_block(shipped, "claude", ("agent.example.com",))
    with_manual = with_agent + "manual.example.com\n"
    root = _make_root(tmp_path, with_manual)
    f = doctor.run_doctor(root, only=["egress"])
    msgs = [msg for _, msg in _egress_msgs(f)]
    baseline = [m for m in msgs if m.endswith("[baseline]")]
    agent = [m for m in msgs if m.endswith("[agent:claude]")]
    manual = [m for m in msgs if m.endswith("[manual]")]
    assert len(baseline) == _baseline_entry_count(shipped)
    assert agent == ["agent.example.com [agent:claude]"]
    assert manual == ["manual.example.com [manual]"]


def test_missing_allowlist_is_a_single_bad_finding(tmp_path: Path) -> None:
    root = _make_root(tmp_path, None)
    f = doctor.run_doctor(root, only=["egress"])
    egress = _egress_msgs(f)
    assert len(egress) == 1
    assert egress[0][0] == doctor.BAD
    assert "allowlist.txt missing" in egress[0][1]
