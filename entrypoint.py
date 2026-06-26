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
import subprocess
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
        sys.exit(
            f"error: {src} not found — authenticate with `claude` on the host first"
        )
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

    # Sync ~/.claude.json — contains organizationUuid and passesEligibilityCache needed
    # for Remote Control eligibility without a network call on startup.
    host_claude_json = Path.home() / ".claude.json"
    if host_claude_json.exists():
        shutil.copy2(host_claude_json, claude_home / ".claude.json")
        print(f"Claude config copied: {host_claude_json} → {claude_home / '.claude.json'}")


# ── container startup ─────────────────────────────────────────────────────────


def ensure_claude_json(claude_home: Path) -> None:
    """
    Claude Code stores onboarding/subscription state in ~/.claude.json (next to ~/.claude/).
    Our bind mount only covers ~/.claude/ (the directory), so we store it as
    ~/.claude/.claude.json and symlink it into place at startup.

    Claude Code never persists `bypassPermissionsModeAccepted`, so spawned RC sessions
    would hang waiting for a one-time accept prompt that never appears.  We force the
    key to true every startup, before execvp.
    """
    home = Path.home()
    stored = claude_home / ".claude.json"
    target = home / ".claude.json"

    if not (target.exists() or target.is_symlink()):
        if not stored.exists():
            stored.parent.mkdir(parents=True, exist_ok=True)
            stored.write_text(
                json.dumps({"hasCompletedOnboarding": True, "lastOnboardingVersion": "1.0"},
                           indent=2)
            )
        target.symlink_to(stored)

    # The file exists – patch the two fields that Claude Code never writes back.
    actual = stored if target.is_symlink() else target
    if actual.exists():
        data = read_json(actual)
        changed = False
        for key in ("bypassPermissionsModeAccepted", "remoteDialogSeen"):
            if not data.get(key):
                data[key] = True
                changed = True
        projects = data.setdefault("projects", {})
        ws = projects.setdefault("/workspace", {})
        if not ws.get("hasTrustDialogAccepted"):
            ws["hasTrustDialogAccepted"] = True
            changed = True
        if changed:
            actual.write_text(json.dumps(data, indent=2))


def ensure_settings(claude_home: Path) -> None:
    p = claude_home / "settings.json"
    if p.exists():
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "theme": "dark",
                "hasCompletedOnboarding": True,
            },
            indent=2,
        )
    )


def configure_git_warden() -> None:
    """Set up global git insteadOf rewrite so canonical GitLab URLs are transparently
    redirected to the Warden inside the container (W3.1). The repo's .git/config
    stays untouched; the rewrite lives only in ~/.gitconfig."""
    gitlab_base = os.environ.get("GITLAB_URL", "https://gitlab.com").rstrip("/") + "/"
    warden_git = os.environ.get("WARDEN_GIT_URL", "http://gitlab-warden:8080/git/").rstrip("/") + "/"
    subprocess.run(
        ["git", "config", "--global", f"url.{warden_git}.insteadOf", gitlab_base],
        check=True,
    )
    os.environ["GIT_TERMINAL_PROMPT"] = "0"


# NOTE (Stufe 01 — Bootstrap-Härtung, R6):
# Die früheren Funktionen configure_git() und configure_gitlab() wurden bewusst entfernt.
# Sie injizierten GitLab-Credentials in den Agent-Container:
#   - configure_git()    schrieb GITLAB_GIT_TOKEN (write_repository) in ~/.netrc
#   - configure_gitlab() registrierte das MCP mit Authorization: Bearer GITLAB_API_TOKEN
# Beide Tokens lagen damit im Prozessraum des Agenten und galten als kompromittiert
# (docs/design/agentic-workflow, §3/§4). Der Agent hält ab jetzt KEIN GitLab-Token.
# GitLab-Zugriff kehrt in Stufe 02 über den Warden zurück (git Smart-HTTP-Proxy + REST-
# Filter); der git-Remote zeigt dann auf den Warden, nicht auf gitlab.com.
# GitHub ist vorerst nicht im Scope (configure_github wurde entfernt).


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


def cmd_start(claude_home: Path) -> None:
    drop_to_dev()
    creds = claude_home / ".credentials.json"
    if not creds.exists():
        sys.exit(
            f"error: {creds} not found.\n"
            f"Run on the host first:\n"
            f"  python3 entrypoint.py sync"
        )

    ensure_claude_json(claude_home)
    ensure_settings(claude_home)
    if os.environ.get("GITLAB_API_URL", "").startswith("http://gitlab-warden"):
        configure_git_warden()

    os.execvp(
        "claude",
        [
            "claude",
            "remote-control",
            "--permission-mode",
            "bypassPermissions",
            "--spawn",
            "same-dir",
            "--debug-file",
            str(claude_home / "rc-debug.log"),
        ],
    )


# ── CLI ───────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--claude-home",
        default=os.environ.get("CLAUDE_HOME", str(Path.home() / ".claude")),
        help="Claude config directory [env: CLAUDE_HOME, default: ~/.claude]",
    )
    sub = parser.add_subparsers(dest="command")

    sync = sub.add_parser(
        "sync", help="Copy .credentials.json from ~/.claude/ into CLAUDE_HOME"
    )
    sync.add_argument(
        "--claude-home",
        default=os.environ.get("CLAUDE_HOME", str(Path(__file__).parent / "claude")),
        help="Target directory [env: CLAUDE_HOME, default: ./claude next to this script]",
    )

    args = parser.parse_args()

    if args.command == "sync":
        cmd_sync(Path(args.claude_home).resolve())
    else:
        cmd_start(Path(args.claude_home).resolve())


if __name__ == "__main__":
    main()
