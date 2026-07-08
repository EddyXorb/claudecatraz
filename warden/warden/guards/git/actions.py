"""The git-namespace action vocabulary: repo/project/instance scopes.

Twenty ids, closed set — no wildcards, no inheritance, config lists replace
completely. DEFAULT is what a host gets when no actions override
applies anywhere in its config cascade; every Criticality.IRREVERSIBLE
action is a compiled-in deny and therefore excluded from DEFAULT by
construction, never by a separate list that could drift.
"""

from __future__ import annotations

from enum import Enum
from typing import Mapping

from ...core.actions import Action, Criticality


class QuotaKind(str, Enum):
    """Which quota bucket a write action spends — drives the R5 accounting and
    the audit log's kind field. Only MR gates anything beyond the blanket
    writes-per-hour rate limit (the open-MR count). A git-namespace concept
    (branches, MRs, CI, issues), so it lives here alongside the vocabulary and
    is carried on each action as an opaque string the core never reads."""

    MR = "mr"
    MR_NOTE = "mr_note"
    MR_UPDATE = "mr_update"
    CI_TRIGGER = "ci_trigger"
    BRANCH_CREATE = "branch_create"
    ISSUE_CREATE = "issue_create"
    ISSUE_UPDATE = "issue_update"
    ISSUE_NOTE = "issue_note"


REPO_READ = Action("repo.read", Criticality.READ)
REPO_BRANCH_CREATE = Action("repo.branch.create", Criticality.WRITE, QuotaKind.BRANCH_CREATE.value)
REPO_BRANCH_PUSH = Action("repo.branch.push", Criticality.WRITE)
REPO_BRANCH_DELETE = Action("repo.branch.delete", Criticality.IRREVERSIBLE)
REPO_TAG_CREATE = Action("repo.tag.create", Criticality.IRREVERSIBLE)
REPO_TAG_DELETE = Action("repo.tag.delete", Criticality.IRREVERSIBLE)

PROJECT_READ = Action("project.read", Criticality.READ)
PROJECT_MR_CREATE = Action("project.mr.create", Criticality.WRITE, QuotaKind.MR.value)
PROJECT_MR_EDIT = Action("project.mr.edit", Criticality.WRITE, QuotaKind.MR_UPDATE.value)
PROJECT_MR_CLOSE = Action("project.mr.close", Criticality.WRITE, QuotaKind.MR_UPDATE.value)
PROJECT_MR_COMMENT = Action("project.mr.comment", Criticality.WRITE, QuotaKind.MR_NOTE.value)
PROJECT_MR_MERGE = Action("project.mr.merge", Criticality.IRREVERSIBLE)
PROJECT_CI_TRIGGER = Action("project.ci.trigger", Criticality.WRITE, QuotaKind.CI_TRIGGER.value)
PROJECT_ISSUE_CREATE = Action(
    "project.issue.create", Criticality.WRITE, QuotaKind.ISSUE_CREATE.value
)
PROJECT_ISSUE_EDIT = Action("project.issue.edit", Criticality.WRITE, QuotaKind.ISSUE_UPDATE.value)
PROJECT_ISSUE_CLOSE = Action("project.issue.close", Criticality.WRITE, QuotaKind.ISSUE_UPDATE.value)
PROJECT_ISSUE_COMMENT = Action(
    "project.issue.comment", Criticality.WRITE, QuotaKind.ISSUE_NOTE.value
)

INSTANCE_PROJECTS_READ = Action("instance.projects.read", Criticality.READ)
INSTANCE_USERS_READ = Action("instance.users.read", Criticality.READ)
INSTANCE_META_READ = Action("instance.meta.read", Criticality.READ)

#: The whole closed vocabulary.
ALL: frozenset[Action] = frozenset(
    {
        REPO_READ,
        REPO_BRANCH_CREATE,
        REPO_BRANCH_PUSH,
        REPO_BRANCH_DELETE,
        REPO_TAG_CREATE,
        REPO_TAG_DELETE,
        PROJECT_READ,
        PROJECT_MR_CREATE,
        PROJECT_MR_EDIT,
        PROJECT_MR_CLOSE,
        PROJECT_MR_COMMENT,
        PROJECT_MR_MERGE,
        PROJECT_CI_TRIGGER,
        PROJECT_ISSUE_CREATE,
        PROJECT_ISSUE_EDIT,
        PROJECT_ISSUE_CLOSE,
        PROJECT_ISSUE_COMMENT,
        INSTANCE_PROJECTS_READ,
        INSTANCE_USERS_READ,
        INSTANCE_META_READ,
    }
)

#: The built-in default: every row marked "yes" in the vocabulary table.
#: Excludes the four never-class (IRREVERSIBLE) actions and the four
#: issue.* opt-in actions.
DEFAULT: frozenset[Action] = frozenset(
    {
        REPO_READ,
        REPO_BRANCH_CREATE,
        REPO_BRANCH_PUSH,
        PROJECT_READ,
        PROJECT_MR_CREATE,
        PROJECT_MR_EDIT,
        PROJECT_MR_CLOSE,
        PROJECT_MR_COMMENT,
        PROJECT_CI_TRIGGER,
        INSTANCE_PROJECTS_READ,
        INSTANCE_USERS_READ,
        INSTANCE_META_READ,
    }
)

by_id: Mapping[str, Action] = {action.id: action for action in ALL}

assert len(by_id) == len(ALL), "duplicate action id in ALL"
assert DEFAULT <= ALL, "DEFAULT must be a subset of ALL"
assert not any(action.criticality is Criticality.IRREVERSIBLE for action in DEFAULT), (
    "DEFAULT must contain no IRREVERSIBLE action"
)
