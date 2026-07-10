"""Quota / sliding-window tests (W14): N ok, N+1 blocks, injected clock (no sleep)."""

from __future__ import annotations

from warden.core.config import Config, GitEndpoint, GitRules, HostCredentials
from warden.core.state import State
from warden.guards.git.gitlab.intent import ApiIntent
from warden.guards.git.gitlab.policy import full_decide as decide

HOST = "gitlab.example"
_OPEN_ENDPOINT = (GitEndpoint(host=HOST, type="gitlab"),)
_OPEN_CREDENTIALS = {HOST: HostCredentials(read_token="r", write_token="w")}


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
        _host=HOST,
    )


def test_n_writes_ok_then_block():
    cfg = Config(
        allowed_projects=("group/proj",),
        # step 04: the stateful ceiling is per-endpoint (Config.effective_rules),
        # never this legacy global field — set it via git_rules to actually bite.
        git_rules=GitRules(max_writes_per_hour=3),
        git_endpoints=_OPEN_ENDPOINT,
        git_credentials=_OPEN_CREDENTIALS,
    )
    clock = FakeClock()
    st = State(":memory:", clock=clock)
    st.mark_reconciled("api")

    for _ in range(3):
        assert decide(_mr(cfg), st.view("api", HOST), cfg).allow
        st.record_write("api", HOST, "mr")
    # (N+1)-th is blocked by the rate limit
    d = decide(_mr(cfg), st.view("api", HOST), cfg)
    assert not d.allow and "rate limit" in d.reason


def test_sliding_window_frees_budget_after_an_hour():
    cfg = Config(
        allowed_projects=("group/proj",),
        git_rules=GitRules(max_writes_per_hour=2),
        git_endpoints=_OPEN_ENDPOINT,
        git_credentials=_OPEN_CREDENTIALS,
    )
    clock = FakeClock()
    st = State(":memory:", clock=clock)
    st.mark_reconciled("api")

    st.record_write("api", HOST, "mr")
    st.record_write("api", HOST, "mr")
    assert not decide(_mr(cfg), st.view("api", HOST), cfg).allow

    clock.advance(3601)  # both records fall out of the 1h window
    assert decide(_mr(cfg), st.view("api", HOST), cfg).allow


def test_locked_until_reconciled():
    cfg = Config(
        allowed_projects=("group/proj",),
        git_endpoints=_OPEN_ENDPOINT,
        git_credentials=_OPEN_CREDENTIALS,
    )
    st = State(":memory:")  # never reconciled
    assert st.view("api", HOST).locked
    assert not decide(_mr(cfg), st.view("api", HOST), cfg).allow

    st.mark_reconciled("api")
    assert not st.view("api", HOST).locked
    assert decide(_mr(cfg), st.view("api", HOST), cfg).allow


# --- per-endpoint quota ceiling (step 04, §3.3): the override wins, not the
# built-in default, and it is scoped to that one endpoint only -----------------


def test_endpoint_override_raises_the_ceiling_above_the_default():
    host_a, host_b = "gitlab.example", "strict.example"
    cfg = Config(
        allowed_projects=("group/proj",),
        git_endpoints=(
            GitEndpoint(host=host_a, type="gitlab", rules=GitRules(max_writes_per_hour=100)),
            GitEndpoint(host=host_b, type="gitlab"),  # falls back to the built-in default (60)
        ),
        git_credentials={
            host_a: HostCredentials(read_token="r", write_token="w"),
            host_b: HostCredentials(read_token="r", write_token="w"),
        },
    )
    assert cfg.effective_rules(host_a).max_writes_per_hour == 100
    assert cfg.effective_rules(host_b).max_writes_per_hour == 60


def test_endpoint_override_lowers_the_ceiling_below_the_default_and_gates_writes():
    host_a, host_b = "gitlab.example", "strict.example"
    cfg = Config(
        allowed_projects=("group/proj",),
        git_endpoints=(
            GitEndpoint(host=host_a, type="gitlab"),  # built-in default (60) — plenty of budget
            GitEndpoint(host=host_b, type="gitlab", rules=GitRules(max_writes_per_hour=1)),
        ),
        git_credentials={
            host_a: HostCredentials(read_token="r", write_token="w"),
            host_b: HostCredentials(read_token="r", write_token="w"),
        },
    )
    st = State(":memory:")
    st.mark_reconciled("api")

    def _mr_for(host: str) -> ApiIntent:
        return ApiIntent(
            _project="group/proj",
            _method="POST",
            path="/projects/group%2Fproj/merge_requests",
            fields={"source_branch": "claude/x"},
            _host=host,
        )

    st.record_write("api", host_b, "mr")
    # host_b's own tight override (1/h) is already exhausted...
    assert not decide(_mr_for(host_b), st.view("api", host_b), cfg).allow
    # ...but host_a's independent, un-overridden budget is untouched by host_b's write.
    assert decide(_mr_for(host_a), st.view("api", host_a), cfg).allow
