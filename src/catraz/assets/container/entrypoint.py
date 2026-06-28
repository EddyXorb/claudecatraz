#!/usr/bin/env python3
"""
Container entrypoint — and host-side credential sync tool.

  python3 entrypoint.py          # inside container: configure + exec claude
  python3 entrypoint.py sync     # on host: copy .credentials.json into CLAUDE_HOME
"""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


def read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _env_true(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


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


def build_claude_home(home: Path, mode: str, remote: bool = True) -> None:
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
    # Remote-Control-only: the daemon would hang on a one-time accept prompt otherwise.
    # In one-off `run` (drop-in claude) mode we keep normal permissions, so skip these.
    if remote:
        data["bypassPermissionsModeAccepted"] = True
        data["remoteDialogSeen"] = True
    data.setdefault("projects", {}).setdefault("/workspace", {})["hasTrustDialogAccepted"] = True
    (Path.home() / ".claude.json").write_text(json.dumps(data, indent=2))
    (home / "settings.json").write_text(
        json.dumps({"theme": "dark", "hasCompletedOnboarding": True}, indent=2))
    install_claude_md(home)


def install_claude_md(home: Path) -> None:
    """Install the agent user-memory (~/.claude/CLAUDE.md) from its read-only source.

    catraz mounts the packaged AGENT.md read-only at ~/.claude/.ro/CLAUDE.md (see
    docker-compose.yml) and we copy it to the writable live path so Claude's `#`
    add-memory shortcut keeps working. The image itself carries NO CLAUDE.md — the
    single source of truth is the asset cache, which also lets a bare `docker run` of
    the image start without the bind for local testing.

    catraz hard-sets REQUIRE_CLAUDE_FILE=true in compose, so on the normal path a
    missing/misconfigured mount fails loud instead of silently starting the agent
    without its guidance.
    """
    src = home / ".ro" / "CLAUDE.md"
    if src.exists():
        shutil.copy2(src, home / "CLAUDE.md")
        return
    if _env_true("REQUIRE_CLAUDE_FILE"):
        sys.exit(
            f"error: REQUIRE_CLAUDE_FILE is set but no CLAUDE.md is mounted at {src}.\n\n"
            "The agent user-memory (CLAUDE.md) is delivered as a read-only bind mount of\n"
            "the packaged AGENT.md from the catraz asset cache. docker-compose.yml expects:\n\n"
            "    - type: bind\n"
            "      source: ${CATRAZ_ASSETS}/AGENT.md\n"
            f"      target: {src}\n"
            "      read_only: true\n\n"
            "Check that:\n"
            "  - you launched the stack via the `catraz` CLI (it sets CATRAZ_ASSETS and\n"
            "    attaches the bind); a bare `docker compose`/`docker run` does not,\n"
            "  - the asset cache contains AGENT.md ($CATRAZ_ASSETS/AGENT.md on the host),\n"
            "  - no compose override removed the mount.\n\n"
            "For local image testing without the bind, leave REQUIRE_CLAUDE_FILE unset."
        )
    # Not required (e.g. bare `docker run` for local testing) -> start without user-memory.


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


# NOTE (Stage 01 — Bootstrap hardening, R6):
# The former configure_git() and configure_gitlab() functions were deliberately removed.
# They injected GitLab credentials into the agent container:
#   - configure_git()    wrote GITLAB_GIT_TOKEN (write_repository) into ~/.netrc
#   - configure_gitlab() registered the MCP with Authorization: Bearer GITLAB_API_TOKEN
# Both tokens were therefore in the agent's process space and considered compromised
# (docs/design/agentic-workflow, §3/§4). The agent no longer holds any GitLab token.
# GitLab access returns in stage 02 via the Warden (git Smart-HTTP proxy + REST
# filter); the git remote will then point to the Warden, not gitlab.com.
# GitHub is out of scope for now (configure_github was removed).


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


def cmd_exec(cmd: list[str]) -> None:
    drop_to_dev()                       # chowns /workspace + re-execs as dev (as in start/run)
    argv = cmd or ["bash"]
    os.execvp(argv[0], argv)


def _resolve_api_key() -> str:
    """Read ANTHROPIC_API_KEY from _FILE (compose secret) falling back to the bare var."""
    file_path = os.environ.get("ANTHROPIC_API_KEY_FILE")
    if file_path:
        try:
            return Path(file_path).read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return os.environ.get("ANTHROPIC_API_KEY", "")


def cmd_start(claude_home: Path) -> None:
    drop_to_dev()
    mode = os.environ.get("AUTH_MODE") or "subscription"
    if mode == "api_key":
        key = _resolve_api_key()
        if not key:
            sys.exit("error: api_key mode but ANTHROPIC_API_KEY unset")
        os.environ["ANTHROPIC_API_KEY"] = key
    build_claude_home(claude_home, mode)
    configure_git_warden()
    spawn = os.environ.get("CLAUDE_RC_SPAWN") or "same-dir"
    debug = os.environ.get("CLAUDE_RC_DEBUG_FILE") or str(claude_home / "rc-debug.log")
    extra = shlex.split(os.environ.get("CLAUDE_RC_EXTRA_ARGS") or "")
    os.execvp("claude", ["claude", "remote-control",
                         "--permission-mode", "bypassPermissions",   # keep-fixed (headless)
                         "--spawn", spawn, "--debug-file", debug, *extra])


def cmd_run(claude_home: Path, claude_args: list[str]) -> None:
    drop_to_dev()
    mode = os.environ.get("AUTH_MODE") or "subscription"
    if mode == "api_key":
        key = _resolve_api_key()
        if not key:
            sys.exit("error: api_key mode but ANTHROPIC_API_KEY unset")
        os.environ["ANTHROPIC_API_KEY"] = key
    build_claude_home(claude_home, mode, remote=False)
    configure_git_warden()
    os.execvp("claude", ["claude", *claude_args])


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

    rn = sub.add_parser("run")
    rn.add_argument("rest", nargs=argparse.REMAINDER)   # ["--", "<args>"...]

    ex = sub.add_parser("exec"); ex.add_argument("rest", nargs=argparse.REMAINDER)

    args = parser.parse_args()

    if args.command == "sync":
        cmd_sync(Path(args.claude_home).resolve(), source=args.source)
        return
    if args.command == "run":
        rest = args.rest[1:] if args.rest and args.rest[0] == "--" else args.rest
        cmd_run(Path(args.claude_home).resolve(), rest)
        return
    if args.command == "exec":
        rest = args.rest[1:] if args.rest[:1] == ["--"] else args.rest
        cmd_exec(rest); return
    cmd_start(Path(args.claude_home).resolve())


if __name__ == "__main__":
    main()
