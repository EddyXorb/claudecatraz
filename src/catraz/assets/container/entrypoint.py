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


def cmd_sync(claude_home: Path, source: str | None = None) -> None:
    src_dir = Path(source or os.environ.get("CLAUDE_CREDENTIAL_SOURCE")
                   or str(Path.home() / ".claude")).expanduser()
    cred = src_dir / ".credentials.json"
    if not cred.exists():
        sys.exit(f"error: {cred} not found — authenticate with `claude` on the host first")
    claude_home.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cred, claude_home / ".credentials.json")
    host_cj = src_dir.parent / ".claude.json"          # ~/.claude.json sits next to ~/.claude/
    dst_cj = claude_home / ".claude.json"
    if host_cj.exists():
        shutil.copy2(host_cj, dst_cj)
    elif not dst_cj.exists():
        dst_cj.write_text(json.dumps(
            {"hasCompletedOnboarding": True, "lastOnboardingVersion": "1.0"}, indent=2))
    print(f"Credentials synced into {claude_home}")


# ── container startup ─────────────────────────────────────────────────────────


def build_home(home: Path, mode: str) -> None:
    """Build the tmpfs Claude-home each start. RO sources live under home/.ro/."""
    home.mkdir(parents=True, exist_ok=True)
    ro = home / ".ro"
    if mode == "subscription":
        src = ro / ".credentials.json"
        if not src.exists():
            sys.exit("error: subscription mode but no .credentials.json mounted (run `catraz sync`)")
        shutil.copy2(src, home / ".credentials.json")
    # .claude.json lives at the HOME ROOT (sibling of ~/.claude), NOT inside the tmpfs dir.
    if mode == "subscription" and (ro / ".claude.json").exists():
        data = read_json(ro / ".claude.json")
    else:
        data = {"hasCompletedOnboarding": True, "lastOnboardingVersion": "1.0"}
    data["bypassPermissionsModeAccepted"] = True
    data["remoteDialogSeen"] = True
    data.setdefault("projects", {}).setdefault("/workspace", {})["hasTrustDialogAccepted"] = True
    (Path.home() / ".claude.json").write_text(json.dumps(data, indent=2))
    (home / "settings.json").write_text(
        json.dumps({"theme": "dark", "hasCompletedOnboarding": True}, indent=2))
    agent_md = Path("/opt/claude-dev-env/AGENT.md")
    if agent_md.exists():
        shutil.copy2(agent_md, home / "CLAUDE.md")


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
    mode = os.environ.get("AUTH_MODE", "subscription")
    if mode == "api_key" and not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("error: api_key mode but ANTHROPIC_API_KEY unset")
    build_home(claude_home, mode)
    configure_git_warden()
    os.execvp("claude", ["claude", "remote-control", "--permission-mode", "bypassPermissions",
                         "--spawn", "same-dir", "--debug-file", str(claude_home / "rc-debug.log")])


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
    sync.add_argument(
        "--from",
        dest="source",
        default=None,
        help="Source ~/.claude dir [env: CLAUDE_CREDENTIAL_SOURCE, default: ~/.claude]",
    )

    args = parser.parse_args()

    if args.command == "sync":
        cmd_sync(Path(args.claude_home).resolve(), source=args.source)
    else:
        cmd_start(Path(args.claude_home).resolve())


if __name__ == "__main__":
    main()
