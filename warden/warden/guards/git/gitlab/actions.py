"""What the GitLab REST guard can enforce: a subset of the git-namespace vocabulary.

SUPPORTED is every project.*/instance.* action plus the two
transport-shared actions this guard also recognizes over REST
(repo.read, repo.branch.create). Accessed qualified
(gitlab.actions.SUPPORTED), never imported bare.
"""

from __future__ import annotations

from enum import Enum
from typing import Mapping

from ....core.actions import Action
from .. import actions as git_actions


class QuotaKind(str, Enum):
    """What a write action touches — drives quota accounting (R5) and the
    audit log's kind field. Only MR gates anything beyond the blanket
    writes-per-hour rate limit (the open-MR count)."""

    MR = "mr"
    MR_NOTE = "mr_note"
    MR_UPDATE = "mr_update"
    CI_TRIGGER = "ci_trigger"
    BRANCH_CREATE = "branch_create"
    ISSUE_CREATE = "issue_create"
    ISSUE_UPDATE = "issue_update"
    ISSUE_NOTE = "issue_note"


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

# Quota kind is a function of the action, not of which recognizer row
# produced it — a request always narrows to a single action by the time
# quota is checked, so keying off the action id here (rather than
# declaring quota_kind on every write recognizer) has nothing left to drift.
# project.mr.merge is deliberately absent: IRREVERSIBLE, denied by the
# criticality gate before any quota check runs.
QUOTA_KIND: Mapping[str, QuotaKind] = {
    git_actions.PROJECT_MR_CREATE.id: QuotaKind.MR,
    git_actions.PROJECT_MR_COMMENT.id: QuotaKind.MR_NOTE,
    git_actions.PROJECT_MR_EDIT.id: QuotaKind.MR_UPDATE,
    git_actions.PROJECT_MR_CLOSE.id: QuotaKind.MR_UPDATE,
    git_actions.PROJECT_CI_TRIGGER.id: QuotaKind.CI_TRIGGER,
    git_actions.REPO_BRANCH_CREATE.id: QuotaKind.BRANCH_CREATE,
    git_actions.PROJECT_ISSUE_CREATE.id: QuotaKind.ISSUE_CREATE,
    git_actions.PROJECT_ISSUE_EDIT.id: QuotaKind.ISSUE_UPDATE,
    git_actions.PROJECT_ISSUE_CLOSE.id: QuotaKind.ISSUE_UPDATE,
    git_actions.PROJECT_ISSUE_COMMENT.id: QuotaKind.ISSUE_NOTE,
}
