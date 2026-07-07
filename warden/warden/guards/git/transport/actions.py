"""What the transport guard can enforce: the repo-scoped git ref actions.

SUPPORTED is the static ceiling — what this guard is capable of gating,
never what an operator has enabled (that is Config.effective_actions).
Always access it qualified (transport.actions.SUPPORTED).
"""

from __future__ import annotations

from ....core.actions import Action
from .. import actions as git_actions

SUPPORTED: frozenset[Action] = frozenset(
    {
        git_actions.REPO_READ,
        git_actions.REPO_BRANCH_CREATE,
        git_actions.REPO_BRANCH_PUSH,
        git_actions.REPO_BRANCH_DELETE,
        git_actions.REPO_TAG_CREATE,
        git_actions.REPO_TAG_DELETE,
    }
)

assert SUPPORTED <= git_actions.ALL, "SUPPORTED must be a subset of the git-namespace vocabulary"
