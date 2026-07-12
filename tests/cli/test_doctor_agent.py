from pathlib import Path

from catraz import doctor
from catraz.paths import agent_state_dir, claude_home


def test_check_agent_reports_active_persistent_mode(tmp_path: Path) -> None:
    """No CLAUDE_CREDENTIALS_MODE override: the shipped claude manifest's
    persistent default is used and reported."""
    state_dir = agent_state_dir(tmp_path, "claude")
    state_dir.mkdir(parents=True, mode=0o700)
    f = doctor.Findings()
    doctor.check_agent(tmp_path, {}, f)
    assert not any(i[0] == doctor.BAD for i in f.items)
    assert any(i[0] == doctor.OK and "credentials mode: persistent" in i[2] for i in f.items)


def test_check_agent_env_override_switches_to_sync_and_reports_it(tmp_path: Path) -> None:
    """CLAUDE_CREDENTIALS_MODE=sync overrides the persistent manifest default
    and check_agent validates the sync-mode sandbox seed instead."""
    home = claude_home(tmp_path)
    home.mkdir(parents=True, mode=0o700)
    (home / ".credentials.json").write_text("{}")
    f = doctor.Findings()
    doctor.check_agent(tmp_path, {"CLAUDE_CREDENTIALS_MODE": "sync"}, f)
    assert not any(i[0] == doctor.BAD for i in f.items)
    assert any(i[0] == doctor.OK and "credentials mode: sync" in i[2] for i in f.items)
    assert any(i[0] == doctor.OK and "sandbox credential present" in i[2] for i in f.items)


def test_check_agent_invalid_env_value_is_bad_but_falls_back_to_manifest(
    tmp_path: Path,
) -> None:
    """An unresolvable CLAUDE_CREDENTIALS_MODE is a doctor finding, never a
    silent mode switch — the manifest default (persistent) still governs."""
    state_dir = agent_state_dir(tmp_path, "claude")
    state_dir.mkdir(parents=True, mode=0o700)
    f = doctor.Findings()
    doctor.check_agent(tmp_path, {"CLAUDE_CREDENTIALS_MODE": "bogus"}, f)
    assert any(i[0] == doctor.BAD and "CLAUDE_CREDENTIALS_MODE" in i[2] for i in f.items)
    assert any(i[0] == doctor.OK and "credentials mode: persistent" in i[2] for i in f.items)
