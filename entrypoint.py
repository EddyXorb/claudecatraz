#!/usr/bin/env python3
"""
Container init script: configure MCP servers from env vars, then exec the given command.
Called as ENTRYPOINT — argv[1:] is exec'd after configuration is done.
"""

import json
import os
import sys
from pathlib import Path


def configure_gitlab() -> None:
    url = os.environ.get("GITLAB_URL", "").strip()
    token = os.environ.get("GITLAB_TOKEN", "").strip()
    if not url or not token:
        return

    p = Path.home() / ".claude" / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)

    cfg = json.loads(p.read_text()) if p.exists() else {}
    cfg.setdefault("mcpServers", {})
    cfg["mcpServers"]["gitlab"] = {
        "type": "http",
        "url": url.rstrip("/") + "/api/v4/mcp",
        "headers": {"Authorization": f"Bearer {token}"},
    }
    p.write_text(json.dumps(cfg, indent=2))
    print(f"GitLab MCP configured: {cfg['mcpServers']['gitlab']['url']}", flush=True)


configure_gitlab()

os.execvp("claude", ["claude", "--dangerously-skip-permissions", "--remote-control"])
