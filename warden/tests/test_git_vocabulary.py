"""The git-namespace action vocabulary: ids, criticality, DEFAULT."""

from __future__ import annotations

from warden.core.actions import Criticality
from warden.guards.git import actions as git_actions

_EXPECTED_CRITICALITY = {
    "repo.read": Criticality.READ,
    "repo.branch.create": Criticality.WRITE,
    "repo.branch.push": Criticality.WRITE,
    "repo.branch.delete": Criticality.IRREVERSIBLE,
    "repo.tag.create": Criticality.IRREVERSIBLE,
    "repo.tag.delete": Criticality.IRREVERSIBLE,
    "project.read": Criticality.READ,
    "project.mr.create": Criticality.WRITE,
    "project.mr.edit": Criticality.WRITE,
    "project.mr.close": Criticality.WRITE,
    "project.mr.comment": Criticality.WRITE,
    "project.mr.merge": Criticality.IRREVERSIBLE,
    "project.ci.trigger": Criticality.WRITE,
    "project.issue.create": Criticality.WRITE,
    "project.issue.edit": Criticality.WRITE,
    "project.issue.close": Criticality.WRITE,
    "project.issue.comment": Criticality.WRITE,
    "instance.projects.read": Criticality.READ,
    "instance.users.read": Criticality.READ,
    "instance.meta.read": Criticality.READ,
}

_DEFAULT_IDS = {
    "repo.read",
    "repo.branch.create",
    "repo.branch.push",
    "project.read",
    "project.mr.create",
    "project.mr.edit",
    "project.mr.close",
    "project.mr.comment",
    "project.ci.trigger",
    "instance.projects.read",
    "instance.users.read",
    "instance.meta.read",
}


def test_vocabulary_has_exactly_twenty_ids() -> None:
    assert len(git_actions.ALL) == 20
    assert {a.id for a in git_actions.ALL} == set(_EXPECTED_CRITICALITY)


def test_ids_are_unique() -> None:
    ids = [a.id for a in git_actions.ALL]
    assert len(ids) == len(set(ids))


def test_by_id_maps_every_id_to_its_action() -> None:
    assert set(git_actions.by_id) == {a.id for a in git_actions.ALL}
    for action_id, action in git_actions.by_id.items():
        assert action.id == action_id


def test_criticality_matches_the_table_per_id() -> None:
    for action in git_actions.ALL:
        assert action.criticality == _EXPECTED_CRITICALITY[action.id], action.id


def test_default_is_exactly_the_check_marked_rows() -> None:
    assert {a.id for a in git_actions.DEFAULT} == _DEFAULT_IDS


def test_default_is_a_subset_of_all() -> None:
    assert git_actions.DEFAULT <= git_actions.ALL


def test_no_irreversible_action_in_default() -> None:
    assert not any(a.criticality is Criticality.IRREVERSIBLE for a in git_actions.DEFAULT)


def test_exactly_four_irreversible_never_class_actions() -> None:
    irreversible = {a.id for a in git_actions.ALL if a.criticality is Criticality.IRREVERSIBLE}
    assert irreversible == {
        "repo.branch.delete",
        "repo.tag.create",
        "repo.tag.delete",
        "project.mr.merge",
    }


def test_exactly_four_opt_in_write_actions_outside_default() -> None:
    non_default_ids = {a.id for a in git_actions.ALL} - {a.id for a in git_actions.DEFAULT}
    irreversible_ids = {a.id for a in git_actions.ALL if a.criticality is Criticality.IRREVERSIBLE}
    assert non_default_ids - irreversible_ids == {
        "project.issue.create",
        "project.issue.edit",
        "project.issue.close",
        "project.issue.comment",
    }
