"""Generic git→Warden routing — agent-agnostic, used by every profile.
Split out of `entrypoint.py` to isolate git remote rewiring from
home/process lifecycle; co-located with it in the built image so a plain
`import git_routing` resolves."""

from __future__ import annotations

import os
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any

# Read-only mount every profile shares for its own policy; read again here
# so routing and rendered instructions never disagree about configured hosts.
DEFAULT_WARDEN_TOML_PATH = Path("/etc/catraz/warden.toml")


def install_host_gitconfig(home: Path) -> None:
    """Seed the writable ~/.gitconfig from the host's, so the agent can commit.
    Copied (not mounted) because `configure_git_warden` later appends insteadOf
    rules to it. A missing host gitconfig mounts as empty and is skipped."""
    src = home / ".ro" / ".gitconfig"
    if src.exists() and src.stat().st_size > 0:
        shutil.copy2(src, Path.home() / ".gitconfig")


def _read_endpoint_hosts(warden_toml_path: Path) -> list[str]:
    """`[[git.endpoint]].host` entries from `warden.toml`, best-effort: a
    missing/unreadable file or malformed TOML/array yields `[]` (route
    nothing) rather than raising, so a misconfigured mount degrades to "git
    isn't routed" instead of crashing the entrypoint before the agent
    starts."""
    try:
        text = warden_toml_path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        data: dict[str, Any] = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    git = data.get("git")
    if not isinstance(git, dict):
        return []
    raw_endpoints = git.get("endpoint")
    if not isinstance(raw_endpoints, list):
        return []
    hosts: list[str] = []
    for raw in raw_endpoints:
        if not isinstance(raw, dict):
            continue
        host = raw.get("host")
        if isinstance(host, str) and host.strip():
            hosts.append(host.strip())
    return hosts


def configure_git_warden(warden_toml_path: Path | None = None) -> None:
    """Set up global git insteadOf rewrites so each configured host in
    `warden_toml_path` routes to the Warden inside the container; the repo's
    .git/config stays untouched, only ~/.gitconfig changes. Needs no SSH
    key — the Warden injects the write-token upstream. An empty endpoint
    list routes nothing."""
    os.environ["GIT_TERMINAL_PROMPT"] = "0"
    path = warden_toml_path or DEFAULT_WARDEN_TOML_PATH
    hosts = _read_endpoint_hosts(path)
    if not hosts:
        print(f"No [[git.endpoint]] configured in {path} — git will not be routed to the warden")
        return
    ssh_user = os.environ.get("GITLAB_SSH_USER", "git")
    for host in hosts:
        warden_base = f"http://{host}:8080/"
        key = f"url.{warden_base}.insteadOf"
        rewrites = [
            f"https://{host}/",  # https://<host>/
            f"{ssh_user}@{host}:",  # scp-like: git@<host>:
            f"ssh://{ssh_user}@{host}/",  # ssh://git@<host>/
        ]
        # --unset-all first keeps re-running idempotent (no duplicate multivar
        # entries); ignore rc=5 "key not found" on a fresh home.
        subprocess.run(["git", "config", "--global", "--unset-all", key], check=False)
        for src in rewrites:
            subprocess.run(["git", "config", "--global", "--add", key, src], check=True)
