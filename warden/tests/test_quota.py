"""Quota / sliding-window tests (W14): N ok, N+1 blocks, injected clock (no sleep)."""

from __future__ import annotations

from warden.core.config import Config
from warden.core.state import State
from warden.guards.gitlab_api.intent import ApiIntent
from warden.guards.gitlab_api.policy import full_decide as decide


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _mr(cfg) -> ApiIntent:
    return ApiIntent(
        _project="group/proj",
        _method="POST",
        path="/projects/group%2Fproj/merge_requests",
        fields={"source_branch": "claude/x"},
    )


def test_n_writes_ok_then_block():
    cfg = Config(
        allowed_projects=("group/proj",), read_token="r", write_token="w", max_writes_per_hour=3
    )
    clock = FakeClock()
    st = State(":memory:", clock=clock)
    st.mark_reconciled("api")

    for _ in range(3):
        assert decide(_mr(cfg), st.view("api"), cfg).allow
        st.record_write("api", "mr")
    # (N+1)-th is blocked by the rate limit
    d = decide(_mr(cfg), st.view("api"), cfg)
    assert not d.allow and d.rule == "R5"


def test_sliding_window_frees_budget_after_an_hour():
    cfg = Config(
        allowed_projects=("group/proj",), read_token="r", write_token="w", max_writes_per_hour=2
    )
    clock = FakeClock()
    st = State(":memory:", clock=clock)
    st.mark_reconciled("api")

    st.record_write("api", "mr")
    st.record_write("api", "mr")
    assert not decide(_mr(cfg), st.view("api"), cfg).allow

    clock.advance(3601)  # both records fall out of the 1h window
    assert decide(_mr(cfg), st.view("api"), cfg).allow


def test_locked_until_reconciled():
    cfg = Config(allowed_projects=("group/proj",), read_token="r", write_token="w")
    st = State(":memory:")  # never reconciled
    assert st.view("api").locked
    assert not decide(_mr(cfg), st.view("api"), cfg).allow

    st.mark_reconciled("api")
    assert not st.view("api").locked
    assert decide(_mr(cfg), st.view("api"), cfg).allow
