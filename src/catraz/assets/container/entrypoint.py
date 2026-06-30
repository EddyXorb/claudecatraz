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
from typing import Any, cast
from urllib.parse import urlsplit


def read_json(p: Path) -> dict[str, Any]:
    try:
        return cast(dict[str, Any], json.loads(p.read_text()))
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
    # A custom config dir (e.g. ~/.claude2) keeps .claude.json INSIDE it; the default
    # ~/.claude layout keeps it as a sibling at ~/.claude.json. Prefer in-dir, then sibling.
    host_cj = src_dir / ".claude.json"
    if not host_cj.exists():
        host_cj = src_dir.parent / ".claude.json"
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
    # Both remote-control and run mode use --permission-mode bypassPermissions, so always
    # pre-accept the one-time dialog.  remoteDialogSeen is only needed for the RC daemon.
    # NOTE: recent Claude Code (≥2.1.x) migrates bypassPermissionsModeAccepted out of
    # .claude.json into settings.json's skipDangerousModePermissionPrompt and then deletes
    # the old key — so the .claude.json flag alone no longer reliably suppresses the prompt.
    # We keep it for older versions but ALSO set the new canonical key below.
    data["bypassPermissionsModeAccepted"] = True
    if remote:
        data["remoteDialogSeen"] = True
    data.setdefault("projects", {}).setdefault("/workspace", {})["hasTrustDialogAccepted"] = True
    (Path.home() / ".claude.json").write_text(json.dumps(data, indent=2))
    # skipDangerousModePermissionPrompt is the field current Claude Code actually checks
    # (userSettings) before showing the bypassPermissions disclaimer — set it directly so
    # the prompt never appears, independent of the .claude.json → settings.json migration.
    (home / "settings.json").write_text(
        json.dumps({
            "theme": "dark",
            "hasCompletedOnboarding": True,
            "skipDangerousModePermissionPrompt": True,
        }, indent=2))
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


def install_host_gitconfig(home: Path) -> None:
    """Seed the writable ~/.gitconfig from the host's, so the agent can commit.

    catraz bind-mounts the host's ~/.gitconfig read-only at home/.ro/.gitconfig (see
    docker-compose.yml) and we copy it to the live ~/.gitconfig. It must be the live
    (writable) file, not a read-only mount there, because configure_git_warden() then
    appends insteadOf rules to it via `git config --global`.

    When no host gitconfig exists the mount is /dev/null, which stages as an empty
    file — skip it (the agent gets no identity, same as before this feature).
    """
    src = home / ".ro" / ".gitconfig"
    if src.exists() and src.stat().st_size > 0:
        shutil.copy2(src, Path.home() / ".gitconfig")


def configure_git_warden() -> None:
    """Set up global git insteadOf rewrites so canonical GitLab URLs are transparently
    redirected to the Warden inside the container (W3.1). The repo's .git/config
    stays untouched; the rewrites live only in ~/.gitconfig.

    All three canonical remote forms are routed to the same warden base — git applies
    the longest matching insteadOf prefix, so HTTPS as well as SSH remotes land on the
    warden's Smart-HTTP endpoint:

        https://gitlab.com/grp/repo.git   (https)
        git@gitlab.com:grp/repo.git       (scp-like ssh)
        ssh://git@gitlab.com/grp/repo.git (ssh://)

    Rewriting SSH → warden-HTTP means the agent needs no SSH key at all: pushes succeed
    because the warden injects the write-token upstream (R1). The SSH user defaults to
    `git` (GITLAB_SSH_USER overrides it for self-hosted instances).

    When GITLAB_MODE=off the rewrite is skipped: the warden denies all GitLab ops in
    that mode, so routing git there would only produce confusing errors. The agent's
    git commands will reach gitlab.com directly (and fail, as expected — no token).
    """
    gitlab_mode = os.environ.get("GITLAB_MODE", "read-write")
    if gitlab_mode == "off":
        print("GitLab disabled (GITLAB_MODE=off) — git will not be routed to the warden")
        os.environ["GIT_TERMINAL_PROMPT"] = "0"
        return
    gitlab_url = os.environ.get("GITLAB_URL", "https://gitlab.com").rstrip("/")
    warden_git = os.environ.get("WARDEN_GIT_URL", "http://gitlab-warden:8080/git/").rstrip("/") + "/"
    host = urlsplit(gitlab_url).hostname or "gitlab.com"
    ssh_user = os.environ.get("GITLAB_SSH_USER", "git")
    rewrites = [
        gitlab_url + "/",             # https://gitlab.com/
        f"{ssh_user}@{host}:",        # git@gitlab.com:   (scp-like)
        f"ssh://{ssh_user}@{host}/",  # ssh://git@gitlab.com/
    ]
    key = f"url.{warden_git}.insteadOf"
    # --unset-all first so re-running on the same ~/.gitconfig stays idempotent
    # (no duplicate multivar entries); ignore the rc=5 "key not found" on a fresh home.
    subprocess.run(["git", "config", "--global", "--unset-all", key], check=False)
    for src in rewrites:
        subprocess.run(["git", "config", "--global", "--add", key, src], check=True)
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


def _resolve_api_key() -> str:
    """Read ANTHROPIC_API_KEY from _FILE (compose secret) falling back to the bare var."""
    file_path = os.environ.get("ANTHROPIC_API_KEY_FILE")
    if file_path:
        try:
            return Path(file_path).read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return os.environ.get("ANTHROPIC_API_KEY", "")


def _bootstrap(claude_home: Path, remote: bool) -> None:
    """Shared per-start setup for every container entry mode (start/run/exec).

    Drops root → dev (chowning /workspace + re-execing via gosu), resolves the auth
    mode — loading ANTHROPIC_API_KEY for api_key mode — rebuilds the tmpfs Claude-home
    and routes git through the warden. `remote` is forwarded to build_claude_home: only
    the remote-control daemon needs the one-time accept prompts pre-dismissed.
    """
    drop_to_dev()
    mode = os.environ.get("AUTH_MODE") or "subscription"
    if mode == "api_key":
        key = _resolve_api_key()
        if not key:
            sys.exit("error: api_key mode but ANTHROPIC_API_KEY unset")
        os.environ["ANTHROPIC_API_KEY"] = key
    build_claude_home(claude_home, mode, remote=remote)
    install_host_gitconfig(claude_home)
    configure_git_warden()


def cmd_exec(claude_home: Path, cmd: list[str]) -> None:
    """Interactive shell / one-off command in the sandbox (`catraz run shell`).

    Lands in the same configured state as claude/claude-remote: full _bootstrap so the
    Claude-home and the git-warden insteadOf rewrite are in place. remote=False — this
    is not the remote-control daemon, so keep normal permissions.
    """
    _bootstrap(claude_home, remote=False)
    argv = cmd or ["bash"]
    os.execvp(argv[0], argv)


def cmd_start(claude_home: Path) -> None:
    _bootstrap(claude_home, remote=True)
    spawn = os.environ.get("CLAUDE_RC_SPAWN") or "same-dir"
    debug = os.environ.get("CLAUDE_RC_DEBUG_FILE") or str(claude_home / "rc-debug.log")
    extra = shlex.split(os.environ.get("CLAUDE_RC_EXTRA_ARGS") or "")
    os.execvp("claude", ["claude", "remote-control",
                         "--dangerously-skip-permissions", # keep-fixed (headless)
                         "--spawn", spawn, "--debug-file", debug, *extra])


def cmd_run(claude_home: Path, claude_args: list[str]) -> None:
    _bootstrap(claude_home, remote=False)
    os.execvp("claude", ["claude", "--dangerously-skip-permissions",  *claude_args])


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
        cmd_exec(Path(args.claude_home).resolve(), rest); return
    cmd_start(Path(args.claude_home).resolve())


if __name__ == "__main__":
    main()
