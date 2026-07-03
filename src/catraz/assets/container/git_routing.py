"""Generic git→Warden routing (§05.2) — agent-agnostic, used by every profile.

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
from pathlib import Path
from urllib.parse import urlsplit


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
    warden_git = (
        os.environ.get("WARDEN_GIT_URL", "http://gitlab-warden:8080/git/").rstrip("/") + "/"
    )
    host = urlsplit(gitlab_url).hostname or "gitlab.com"
    ssh_user = os.environ.get("GITLAB_SSH_USER", "git")
    rewrites = [
        gitlab_url + "/",  # https://gitlab.com/
        f"{ssh_user}@{host}:",  # git@gitlab.com:   (scp-like)
        f"ssh://{ssh_user}@{host}/",  # ssh://git@gitlab.com/
    ]
    key = f"url.{warden_git}.insteadOf"
    # --unset-all first so re-running on the same ~/.gitconfig stays idempotent
    # (no duplicate multivar entries); ignore the rc=5 "key not found" on a fresh home.
    subprocess.run(["git", "config", "--global", "--unset-all", key], check=False)
    for src in rewrites:
        subprocess.run(["git", "config", "--global", "--add", key, src], check=True)
    os.environ["GIT_TERMINAL_PROMPT"] = "0"
