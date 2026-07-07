"""Tests for the core action model: Criticality, Action, Recognizer, first_recognized."""

from __future__ import annotations

from dataclasses import dataclass

from warden.core.actions import Action, Criticality
from warden.core.recognizer import Recognizer, first_recognized

# --- Criticality --------------------------------------------------------


def test_criticality_orders_read_below_write_below_irreversible():
    assert Criticality.READ < Criticality.WRITE < Criticality.IRREVERSIBLE


# --- Action --------------------------------------------------------------


def test_action_equality_and_hash_are_by_value():
    a = Action(id="x.y", criticality=Criticality.WRITE)
    b = Action(id="x.y", criticality=Criticality.WRITE)
    c = Action(id="x.z", criticality=Criticality.WRITE)

    assert a == b
    assert hash(a) == hash(b)
    assert a != c


def test_action_is_usable_as_a_frozenset_member():
    a = Action(id="x.y", criticality=Criticality.READ)
    b = Action(id="x.y", criticality=Criticality.READ)
    c = Action(id="x.z", criticality=Criticality.WRITE)

    actions = frozenset({a, b, c})

    assert actions == frozenset({a, c})
    assert a in actions


# --- Recognizer / first_recognized ----------------------------------------


@dataclass(frozen=True)
class _DummyIntent:
    writes: bool
    project: str
    method: str
    host: str


def _intent(method: str) -> _DummyIntent:
    return _DummyIntent(writes=False, project="group/proj", method=method, host="example.test")


class _DummyRecognizer(Recognizer[_DummyIntent]):
    def __init__(self, id: str, method: str, action_ids: frozenset[str]) -> None:
        self.id = id
        self._method = method
        self._actions = frozenset(
            Action(id=action_id, criticality=Criticality.READ) for action_id in action_ids
        )

    def __call__(self, intent: _DummyIntent) -> frozenset[Action] | None:
        return self._actions if intent.method == self._method else None


def test_first_recognized_returns_the_first_of_two_overlapping_rows():
    first = _DummyRecognizer("first", "GET", frozenset({"x.read"}))
    second = _DummyRecognizer("second", "GET", frozenset({"x.other"}))

    # the *first* row wins, not a union of both
    result = first_recognized([first, second], _intent("GET"))
    assert result is not None
    assert {a.id for a in result} == {"x.read"}


def test_first_recognized_returns_none_when_nothing_matches():
    catalog = [_DummyRecognizer("only", "GET", frozenset({"x.read"}))]

    assert first_recognized(catalog, _intent("POST")) is None


def test_matched_recognizer_may_legally_recognize_no_action():
    recognizer = _DummyRecognizer("empty", "GET", frozenset())
    intent = _intent("GET")

    assert recognizer(intent) == frozenset()
