from pathlib import Path
from typing import Any


def test_sync_materializes_claude_json(ep: Any, tmp_path: Path) -> None:
    src = tmp_path/"src"/".claude"; src.mkdir(parents=True)
    (src/".credentials.json").write_text("{}")                 # no host ~/.claude.json
    home = tmp_path/"dst"
    ep.cmd_sync(home, source=str(src))
    assert (home/".credentials.json").exists()
    assert (home/".claude.json").exists()                      # synthesized default
