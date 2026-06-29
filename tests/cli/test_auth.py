from pathlib import Path
from catraz import auth, doctor
from catraz.errors import CliError
import pytest

def _mk(tmp_path: Path, env: str) -> Path:
    (tmp_path/".catraz").mkdir(); (tmp_path/".catraz/.env").write_text(env)
    (tmp_path/".catraz/claude").mkdir()
    return tmp_path

def test_mode_invalid(tmp_path: Path) -> None:
    _mk(tmp_path, "AUTH_MODE=both\n")
    with pytest.raises(CliError): auth.auth_mode(tmp_path)

def test_fragment_subscription(tmp_path: Path) -> None:
    _mk(tmp_path, "AUTH_MODE=subscription\n"); auth.write_auth_fragment(tmp_path)
    assert ".credentials.json" in (tmp_path/".catraz/.auth.compose.yml").read_text()

def test_fragment_api_key(tmp_path: Path) -> None:
    _mk(tmp_path, "AUTH_MODE=api_key\n"); auth.write_auth_fragment(tmp_path)
    assert "ANTHROPIC_API_KEY" in (tmp_path/".catraz/.auth.compose.yml").read_text()

def test_doctor_auth_xor(tmp_path: Path) -> None:
    root = _mk(tmp_path, "")
    f = doctor.Findings()
    doctor.check_auth(root, {"AUTH_MODE":"api_key","ANTHROPIC_API_KEY":"x"}, f)
    (root/".catraz/claude/.credentials.json").write_text("{}")
    f2 = doctor.Findings(); doctor.check_auth(root, {"AUTH_MODE":"api_key","ANTHROPIC_API_KEY":"x"}, f2)
    assert any(i[0]==doctor.BAD for i in f2.items)   # cred present in api_key → bad


def test_doctor_auth_warns_about_refresh_persistence(tmp_path: Path) -> None:
    from catraz import doctor
    (tmp_path/".catraz/claude").mkdir(parents=True)
    (tmp_path/".catraz/claude/.credentials.json").write_text("{}")
    f = doctor.Findings()
    doctor.check_auth(tmp_path, {"AUTH_MODE": "subscription"}, f)
    # tie the assertion to the auth SECTION so a misplaced warn elsewhere can't pass it
    assert any(i[0] == doctor.WARN and i[1] == "auth" and "persist" in i[2].lower()
               for i in f.items)


def test_doctor_auth_absent_auth_mode_is_subscription(tmp_path: Path) -> None:
    """AUTH_MODE absent → defaults to subscription (no bad finding)."""
    from catraz import doctor
    (tmp_path/".catraz/claude").mkdir(parents=True)
    (tmp_path/".catraz/claude/.credentials.json").write_text("{}")
    f = doctor.Findings()
    doctor.check_auth(tmp_path, {}, f)   # no AUTH_MODE key
    assert not any(i[0] == doctor.BAD for i in f.items)


def test_doctor_auth_empty_auth_mode_is_subscription(tmp_path: Path) -> None:
    """AUTH_MODE="" → defaults to subscription (no bad finding)."""
    from catraz import doctor
    (tmp_path/".catraz/claude").mkdir(parents=True)
    (tmp_path/".catraz/claude/.credentials.json").write_text("{}")
    f = doctor.Findings()
    doctor.check_auth(tmp_path, {"AUTH_MODE": ""}, f)
    assert not any(i[0] == doctor.BAD for i in f.items)


def test_doctor_auth_bogus_mode_is_bad(tmp_path: Path) -> None:
    """AUTH_MODE=bogus → bad finding."""
    from catraz import doctor
    f = doctor.Findings()
    doctor.check_auth(tmp_path, {"AUTH_MODE": "bogus"}, f)
    assert any(i[0] == doctor.BAD for i in f.items)


def test_doctor_auth_api_key_with_key(tmp_path: Path) -> None:
    """api_key mode with key set and no cred file → ok."""
    from catraz import doctor
    (tmp_path/".catraz/claude").mkdir(parents=True)
    f = doctor.Findings()
    doctor.check_auth(tmp_path, {"AUTH_MODE": "api_key", "ANTHROPIC_API_KEY": "sk-x"}, f)
    assert not any(i[0] == doctor.BAD for i in f.items)
