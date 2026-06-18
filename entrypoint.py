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

    Claude Code never persists `bypassPermissionsModeAccepted`, so spawned RC sessions
    would hang waiting for a one-time accept prompt that never appears.  We force the
    key to true every startup, before execvp.
    """
    home = Path.home()
    stored = home / ".claude" / ".claude.json"
    target = home / ".claude.json"

    if not (target.exists() or target.is_symlink()):
        if not stored.exists():
            stored.parent.mkdir(parents=True, exist_ok=True)
            stored.write_text(json.dumps({
                "hasCompletedOnboarding": True,
                "lastOnboardingVersion": "1.0",
                "bypassPermissionsModeAccepted": True,
                "projects": {
                    "/workspace": {"hasTrustDialogAccepted": True},
                },
            }, indent=2))
        target.symlink_to(stored)

    # The file exists – patch the two fields that Claude Code never writes back.
    actual = stored if target.is_symlink() else target
    if actual.exists():
        data = read_json(actual)
        changed = False
        if not data.get("bypassPermissionsModeAccepted"):
            data["bypassPermissionsModeAccepted"] = True
            changed = True
        projects = data.setdefault("projects", {})
        ws = projects.setdefault("/workspace", {})
        if not ws.get("hasTrustDialogAccepted"):
            ws["hasTrustDialogAccepted"] = True
            changed = True
        if changed:
            actual.write_text(json.dumps(data, indent=2))


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
    token = os.environ.get("GITLAB_TOKEN", "").strip()
    if not token:
        return

    import subprocess
    # Remove stale entry first (idempotent — ignore errors if it doesn't exist)
    subprocess.run(["claude", "mcp", "remove", "gitlab"], capture_output=True)
    result = subprocess.run(
        [
            "claude", "mcp", "add",
            "--transport", "http",
            "gitlab",
            "http://gitlab-mcp:3002/mcp",
            "--header", f"Authorization: Bearer {token}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print("GitLab MCP registered: http://gitlab-mcp:3002/mcp", flush=True)
    else:
        print(f"GitLab MCP registration failed: {result.stderr.strip()}", flush=True)


def drop_to_dev() -> None:
    """If running as root, fix /workspace ownership and re-exec as the dev user via gosu."""
    if os.getuid() != 0:
        return
    import pwd
    try:
        pw = pwd.getpwnam("dev")
    except KeyError:
        sys.exit("error: user 'dev' not found in container")

    workspace = Path("/workspace")
    if workspace.exists():
        os.chown(workspace, pw.pw_uid, pw.pw_gid)

    os.execvp("gosu", ["gosu", "dev", sys.executable] + sys.argv)


def cmd_start() -> None:
    drop_to_dev()
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

    os.execvp("claude", [
        "claude",
        "--dangerously-skip-permissions",
        "--remote-control",
        "--permission-mode", "bypassPermissions",
        "--debug-file", "/home/dev/.claude/rc-debug.log",
    ])


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
