def test_build_home_oneoff_run_no_bypass(ep, tmp_path, monkeypatch):
    home = tmp_path/".claude"; home.mkdir()
    monkeypatch.setattr(ep.Path, "home", staticmethod(lambda: tmp_path))
    ep.build_home(home, "api_key", remote=False)
    import json; cj = json.loads((tmp_path/".claude.json").read_text())
    assert "bypassPermissionsModeAccepted" not in cj
