"""What the GitLab REST guard can enforce: a subset of the git-namespace vocabulary.

SUPPORTED is every project.*/instance.* action plus the two
transport-shared actions this guard also recognizes over REST
(repo.read, repo.branch.create). Accessed qualified
(gitlab.actions.SUPPORTED), never imported bare.
"""

from __future__ import annotations

from ....core.actions import Action
from .. import actions as git_actions

SUPPORTED: frozenset[Action] = frozenset(
    {
        git_actions.REPO_READ,
        git_actions.REPO_BRANCH_CREATE,
        git_actions.PROJECT_READ,
        git_actions.PROJECT_MR_CREATE,
        git_actions.PROJECT_MR_EDIT,
        git_actions.PROJECT_MR_CLOSE,
        git_actions.PROJECT_MR_COMMENT,
        git_actions.PROJECT_MR_MERGE,
        git_actions.PROJECT_CI_TRIGGER,
        git_actions.PROJECT_ISSUE_CREATE,
        git_actions.PROJECT_ISSUE_EDIT,
        git_actions.PROJECT_ISSUE_CLOSE,
        git_actions.PROJECT_ISSUE_COMMENT,
        git_actions.INSTANCE_PROJECTS_READ,
        git_actions.INSTANCE_USERS_READ,
        git_actions.INSTANCE_META_READ,
    }
)

assert SUPPORTED <= git_actions.ALL, "gitlab guard SUPPORTED must be a subset of the vocabulary"
