from pathlib import Path
from typing import Any
import pytest
from catraz import doctor, image

def test_base_contract_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(doctor, "which", lambda c: True)
    monkeypatch.setattr(image, "resolve_base", lambda r: "base:tag")
    class R:
        def __init__(s: Any, rc: int, out: str = "") -> None:
            s.returncode, s.stdout = rc, out
    monkeypatch.setattr(doctor.subprocess, "run",  # type: ignore[attr-defined]
        lambda cmd, **k: R(1) if "apt-get" in " ".join(cmd) else R(0, ""))
    f = doctor.Findings(); doctor.check_base(tmp_path, {}, f)
    assert any(i[0]==doctor.BAD for i in f.items)
