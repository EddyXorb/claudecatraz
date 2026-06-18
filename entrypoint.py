#!/usr/bin/env python3
"""
Container entrypoint — and host-side credential sync tool.

  python3 entrypoint.py          # inside container: configure + exec claude
  python3 entrypoint.py sync     # on host: copy .credentials.json into CLAUDE_HOME
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path


def read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


# ── host-side sync ────────────────────────────────────────────────────────────

def cmd_sync(claude_home: Path) -> None:
    src = Path.home() / ".claude" / ".credentials.json"
    dst = claude_home / ".credentials.json"

    if not src.exists():
        sys.exit(f"error: {src} not found — authenticate with `claude` on the host first")
    if claude_home.exists() and claude_home.stat().st_uid == 0:
        sys.exit(
            f"error: {claude_home} is owned by root (Docker created it automatically).\n"
            f"Fix with:\n"
            f"  sudo rm -rf {claude_home}\n"
            f"  mkdir -p {claude_home}"
        )

    claude_home.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    print(f"Credentials copied: {src} → {dst}")


# ── container startup ─────────────────────────────────────────────────────────

def ensure_claude_json() -> None:
    """
    Claude Code stores onboarding/subscription state in ~/.claude.json (next to ~/.claude/).
    Our bind mount only covers ~/.claude/ (the directory), so we store it as
    ~/.claude/.claude.json and symlink it into place at startup.
    """
    home = Path.home()
    stored = home / ".claude" / ".claude.json"
    target = home / ".claude.json"

    if target.exists() or target.is_symlink():
        return

    if not stored.exists():
        stored.parent.mkdir(parents=True, exist_ok=True)
        stored.write_text(json.dumps({
            "hasCompletedOnboarding": True,
            "lastOnboardingVersion": "1.0",
            "bypassPermissionsModeAccepted": True,
            "projects": {
                "/workspace": {
                    "hasTrustDialogAccepted": True,
                }
            },
        }, indent=2))

    target.symlink_to(stored)


def ensure_settings() -> None:
    p = Path.home() / ".claude" / "settings.json"
    if p.exists():
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "theme": "dark",
        "hasCompletedOnboarding": True,
    }, indent=2))


def configure_gitlab() -> None:
    url = os.environ.get("GITLAB_URL", "").strip()
    token = os.environ.get("GITLAB_TOKEN", "").strip()
    if not url or not token:
        return

    p = Path.home() / ".claude" / "settings.json"
    cfg = read_json(p)
    cfg.setdefault("mcpServers", {})
    cfg["mcpServers"]["gitlab"] = {
        "type": "http",
        "url": url.rstrip("/") + "/api/v4/mcp",
        "headers": {"Authorization": f"Bearer {token}"},
    }
    p.write_text(json.dumps(cfg, indent=2))
    print(f"GitLab MCP configured: {cfg['mcpServers']['gitlab']['url']}", flush=True)


def cmd_start() -> None:
    creds = Path.home() / ".claude" / ".credentials.json"
    if not creds.exists():
        sys.exit(
            f"error: {creds} not found.\n"
            f"Run on the host first:\n"
            f"  python3 entrypoint.py sync"
        )

    ensure_claude_json()
    ensure_settings()
    configure_gitlab()

    os.execvp("claude", ["claude", "--dangerously-skip-permissions", "--remote-control"])


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")

    sync = sub.add_parser("sync", help="Copy .credentials.json from ~/.claude/ into CLAUDE_HOME")
    sync.add_argument(
        "--claude-home",
        default=os.environ.get("CLAUDE_HOME", str(Path(__file__).parent / "claude")),
        help="Target directory [env: CLAUDE_HOME, default: ./claude next to this script]",
    )

    args = parser.parse_args()

    if args.command == "sync":
        cmd_sync(Path(args.claude_home).resolve())
    else:
        cmd_start()


if __name__ == "__main__":
    main()
