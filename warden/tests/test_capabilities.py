"""Golden tests for the capability-invariant layer in isolation.

Neither guard consumes this layer any more — irreversible actions are denied
by their recognized action's criticality instead (see
``tests/transport/test_recognizers.py`` and ``tests/gitlab/test_recognizers.py``).
``core.capabilities`` itself is still present, pending removal.
"""

from __future__ import annotations

import pytest

from warden.core.capabilities import FORBIDDEN, Capability, forbidden_check

# --- the vocabulary itself ------------------------------------------------


def test_capability_vocabulary_is_exactly_the_documented_set():
    assert {c.value for c in Capability} == {
        "creates_ref",
        "deletes_ref",
        "creates_tag",
        "merges",
        "escalates_privilege",
        "writes_outside_namespace",
        "destroys_data",
    }


def test_forbidden_is_a_frozenset_with_exactly_the_documented_members():
    # Guards against accidental widening/narrowing of the compiled-in
    # invariant: never configurable, so this must stay a hard-coded constant
    # this test can pin down.
    assert isinstance(FORBIDDEN, frozenset)
    assert FORBIDDEN == {
        Capability.DELETES_REF,
        Capability.CREATES_TAG,
        Capability.MERGES,
        Capability.ESCALATES_PRIVILEGE,
        Capability.DESTROYS_DATA,
    }
    # creates_ref and writes_outside_namespace are in the vocabulary but not
    # forbidden (see FORBIDDEN's docstring) — pin that down too.
    assert Capability.CREATES_REF not in FORBIDDEN
    assert Capability.WRITES_OUTSIDE_NAMESPACE not in FORBIDDEN


# --- forbidden_check, the layer in isolation -------------------------------


@pytest.mark.parametrize(
    "cap",
    [
        Capability.DELETES_REF,
        Capability.CREATES_TAG,
        Capability.MERGES,
        Capability.ESCALATES_PRIVILEGE,
        Capability.DESTROYS_DATA,
    ],
)
def test_forbidden_check_denies_each_forbidden_capability_with_r4(cap):
    d = forbidden_check(frozenset({cap}))
    assert d is not None
    assert not d.allow and d.rule == "R4"
    assert cap.value in d.reason


@pytest.mark.parametrize(
    "caps",
    [
        frozenset(),
        frozenset({Capability.CREATES_REF}),
        frozenset({Capability.WRITES_OUTSIDE_NAMESPACE}),
        frozenset({Capability.CREATES_REF, Capability.WRITES_OUTSIDE_NAMESPACE}),
    ],
)
def test_forbidden_check_passes_non_forbidden_capabilities(caps):
    assert forbidden_check(caps) is None


def test_forbidden_check_names_every_violated_capability():
    d = forbidden_check(frozenset({Capability.MERGES, Capability.DELETES_REF}))
    assert d is not None
    assert "merges" in d.reason and "deletes_ref" in d.reason
