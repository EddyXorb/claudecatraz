"""The git-namespace action vocabulary: repo/project/instance scopes.

Twenty ids, closed set — no wildcards, no inheritance, config lists replace
completely. ``DEFAULT`` is what a host gets when no ``actions`` override
applies anywhere in its config cascade; every ``Criticality.IRREVERSIBLE``
action is a compiled-in deny and therefore excluded from ``DEFAULT`` by
construction, never by a separate list that could drift.
"""

from __future__ import annotations

from typing import Mapping

from ...core.actions import Action, Criticality

REPO_READ = Action("repo.read", Criticality.READ)
REPO_BRANCH_CREATE = Action("repo.branch.create", Criticality.WRITE)
REPO_BRANCH_PUSH = Action("repo.branch.push", Criticality.WRITE)
REPO_BRANCH_DELETE = Action("repo.branch.delete", Criticality.IRREVERSIBLE)
REPO_TAG_CREATE = Action("repo.tag.create", Criticality.IRREVERSIBLE)
REPO_TAG_DELETE = Action("repo.tag.delete", Criticality.IRREVERSIBLE)

PROJECT_READ = Action("project.read", Criticality.READ)
PROJECT_MR_CREATE = Action("project.mr.create", Criticality.WRITE)
PROJECT_MR_EDIT = Action("project.mr.edit", Criticality.WRITE)
PROJECT_MR_CLOSE = Action("project.mr.close", Criticality.WRITE)
PROJECT_MR_COMMENT = Action("project.mr.comment", Criticality.WRITE)
PROJECT_MR_MERGE = Action("project.mr.merge", Criticality.IRREVERSIBLE)
PROJECT_CI_TRIGGER = Action("project.ci.trigger", Criticality.WRITE)
PROJECT_ISSUE_CREATE = Action("project.issue.create", Criticality.WRITE)
PROJECT_ISSUE_EDIT = Action("project.issue.edit", Criticality.WRITE)
PROJECT_ISSUE_CLOSE = Action("project.issue.close", Criticality.WRITE)
PROJECT_ISSUE_COMMENT = Action("project.issue.comment", Criticality.WRITE)

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
