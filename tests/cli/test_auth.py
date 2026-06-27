from catraz import auth, doctor
from catraz.errors import CliError
import pytest

def _mk(tmp_path, env):
    (tmp_path/".catraz").mkdir(); (tmp_path/".catraz/.env").write_text(env)
    (tmp_path/".catraz/claude").mkdir()
    return tmp_path

def test_mode_invalid(tmp_path):
    _mk(tmp_path, "AUTH_MODE=both\n")
    with pytest.raises(CliError): auth.auth_mode(tmp_path)

def test_fragment_subscription(tmp_path):
    _mk(tmp_path, "AUTH_MODE=subscription\n"); auth.write_auth_fragment(tmp_path)
    assert ".credentials.json" in (tmp_path/".catraz/.auth.compose.yml").read_text()

def test_fragment_api_key(tmp_path):
    _mk(tmp_path, "AUTH_MODE=api_key\n"); auth.write_auth_fragment(tmp_path)
    assert "ANTHROPIC_API_KEY" in (tmp_path/".catraz/.auth.compose.yml").read_text()

def test_doctor_auth_xor(tmp_path):
    root = _mk(tmp_path, "")
    f = doctor.Findings()
    doctor.check_auth(root, {"AUTH_MODE":"api_key","ANTHROPIC_API_KEY":"x"}, f)
    (root/".catraz/claude/.credentials.json").write_text("{}")
    f2 = doctor.Findings(); doctor.check_auth(root, {"AUTH_MODE":"api_key","ANTHROPIC_API_KEY":"x"}, f2)
    assert any(i[0]==doctor.BAD for i in f2.items)   # cred present in api_key → bad
