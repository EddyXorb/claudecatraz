"""Generic git→Warden routing (§05.2, §07 multi-host cutover) — agent-agnostic,
used by every profile.

Split out of ``entrypoint.py`` purely to keep that module under the
clean-code line budget (SRP: home/process lifecycle vs. git remote rewiring
are different concerns). Co-located next to ``entrypoint.py`` in the built
image (see ``assets/agents/<name>/layer.Dockerfile``) so a plain
``import git_routing`` resolves.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any

# Same mount every profile's compose service uses for the agent's read-only
# view of its own policy (docker-compose.yml, entrypoint._instruction_context).
# Read again here so git routing and the rendered instructions can never
# disagree about which hosts are configured — one file, one source of truth.
DEFAULT_WARDEN_TOML_PATH = Path("/etc/catraz/warden.toml")


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


def _read_endpoint_hosts(warden_toml_path: Path) -> list[str]:
    """``[[git.endpoint]].host`` entries from ``warden.toml``, best-effort.

    Host-side-of-the-container concern, not the Warden's own fail-closed
    parser (``warden/warden/core/config_load.py``): a missing/unreadable file
    or malformed TOML/array yields ``[]`` (route nothing) rather than raising
    — a misconfigured mount should degrade to "git isn't routed" (the agent's
    own `git` commands then simply fail against the real internet, same as
    today's no-token case), not crash the entrypoint before the agent even
    starts.
    """
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
    """Set up global git insteadOf rewrites so every configured canonical git
    host is transparently redirected to the Warden inside the container
    (§1.1/§1.2 of ``docs/design/architecture-generalization/08-multi-target.md``).
    The repo's .git/config stays untouched; the rewrites live only in
    ~/.gitconfig.

    **Schema-rewrite, not a path trick.** Unlike the old single-target form
    (which rewrote the hostname itself to ``gitlab-warden`` and added a
    ``/git/`` path prefix), each configured host keeps its own canonical name
    — DNS (the compose-rendered per-host alias, ``07-compose-and-agent-
    routing.md``) resolves that name to the Warden container, and only the
    scheme+port change:

        https://my-gitlab.de/   →   http://my-gitlab.de:8080/
        https://gitlab.com/     →   http://gitlab.com:8080/

    For each host, all three canonical remote forms are routed to the same
    per-host Warden base — git applies the longest matching insteadOf prefix,
    so HTTPS as well as SSH remotes land on the warden's Smart-HTTP endpoint:

        https://<host>/grp/repo.git    (https)
        git@<host>:grp/repo.git        (scp-like ssh)
        ssh://git@<host>/grp/repo.git  (ssh://)

    Rewriting SSH → warden-HTTP means the agent needs no SSH key at all: pushes
    succeed because the warden injects the write-token upstream (R1). The SSH
    user defaults to `git` (GITLAB_SSH_USER overrides it for every host — a
    per-host SSH user isn't part of the endpoint schema).

    The host list comes from ``warden_toml_path`` (default: the same
    ``/etc/catraz/warden.toml`` mount the rendered instructions read) —
    **no** ``[[git.endpoint]]`` entries means nothing to route, which is the
    direct replacement for the old ``GITLAB_MODE=off`` special case: an empty
    endpoint list already routes nothing, no separate off-switch needed.
    """
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
        # --unset-all first so re-running on the same ~/.gitconfig stays idempotent
        # (no duplicate multivar entries); ignore the rc=5 "key not found" on a
        # fresh home or a host seen for the first time.
        subprocess.run(["git", "config", "--global", "--unset-all", key], check=False)
        for src in rewrites:
            subprocess.run(["git", "config", "--global", "--add", key, src], check=True)
