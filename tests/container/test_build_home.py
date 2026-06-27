def test_build_home_subscription(ep, tmp_path, monkeypatch):
    home = tmp_path / ".claude"; (home/".ro").mkdir(parents=True)
    (home/".ro"/".credentials.json").write_text("{}")
    (home/".ro"/".claude.json").write_text('{"organizationUuid":"org"}')
    monkeypatch.setattr(ep.Path, "home", staticmethod(lambda: tmp_path))
    ep.build_home(home, "subscription")
    assert (home/".credentials.json").exists()
    import json; cj = json.loads((tmp_path/".claude.json").read_text())
    assert cj["organizationUuid"] == "org" and cj["bypassPermissionsModeAccepted"] is True
    assert (home/"settings.json").exists()

def test_build_home_api_key_synthesizes(ep, tmp_path, monkeypatch):
    home = tmp_path / ".claude"; home.mkdir()
    monkeypatch.setattr(ep.Path, "home", staticmethod(lambda: tmp_path))
    ep.build_home(home, "api_key")
    assert not (home/".credentials.json").exists()
    assert (tmp_path/".claude.json").exists()
