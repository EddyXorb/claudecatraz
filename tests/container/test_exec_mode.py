def test_exec_default_bash(ep, monkeypatch):
    calls = []
    monkeypatch.setattr(ep, "drop_to_dev", lambda: None)
    monkeypatch.setattr(ep.os, "execvp", lambda prog, argv: calls.append((prog, argv)))
    ep.cmd_exec([])
    assert calls == [("bash", ["bash"])]


def test_exec_passthrough(ep, monkeypatch):
    calls = []
    monkeypatch.setattr(ep, "drop_to_dev", lambda: None)
    monkeypatch.setattr(ep.os, "execvp", lambda prog, argv: calls.append((prog, argv)))
    ep.cmd_exec(["ls", "-la"])
    assert calls == [("ls", ["ls", "-la"])]
