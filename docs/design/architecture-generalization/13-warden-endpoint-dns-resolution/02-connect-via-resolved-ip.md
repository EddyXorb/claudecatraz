# 02 — connect-via-resolved-ip

Derives from [`../13-warden-endpoint-dns-resolution.md`](../13-warden-endpoint-dns-resolution.md)
§2, §4. On contradiction the main doc wins — report it, do not guess. Depends
on [01-controlled-resolver](01-controlled-resolver.md). Security-sensitive:
the fail-closed deny+audit and the "never connects to its own alias" test
land in this one commit.

## How

Every upstream socket the warden opens goes through `httpx.AsyncClient` in
`warden/warden/core/transport.py` (`Upstream`'s four request methods,
`get_json`, `open_rest`, `git_get`, `git_post_stream` — shared by the git
Smart-HTTP guard and the GitLab REST guard, so one change covers both). The
mechanism operates at the `httpx.Request` level, not a custom network
backend: `httpx.AsyncHTTPTransport` (pinned httpx 0.28.1) does not expose a
`network_backend` constructor parameter, and a lower-level custom transport
would be invisible to `respx`, which the whole existing test suite uses to
mock upstream traffic.

* `Upstream.__init__` gains `host: str` (the endpoint's real hostname —
  today only embedded inside `git_base`/`api_base`) and
  `resolver: HostResolver`.
* New private helper, e.g. `async def _pinned(self, url: str) -> tuple[str,
  dict[str, Any]]`: calls `await self._resolver.resolve(self._host)`,
  returns `(str(httpx.URL(url).copy_with(host=ip)), {"extensions":
  {"sni_hostname": self._host}})`. `ResolutionError` is not caught here — it
  propagates straight out of `Upstream`, uncaught, all the way to
  `Guard.handle`.
* Each of the four methods calls `_pinned` first, then builds the request
  against the swapped URL, merging in `extensions={"sni_hostname":
  self._host}` and an explicit `"Host": self._host` header (added
  unconditionally — `httpx.Request._prepare` only auto-derives `Host` `if
  not has_host`, so this explicit header always wins over the swapped URL's
  IP-based netloc). `git_post_stream`'s streaming body and
  `open_rest`'s `stream=True` send are unaffected — only the target
  URL/headers/extensions passed into `build_request`/`.get(...)` change.
* `UpstreamRouter.__init__` builds one resolver instance (`DnsResolver`,
  from `cfg.dns_resolver`) shared by every `Upstream`, adds a keyword-only
  `resolver: Optional[HostResolver] = None` parameter (defaulting to that
  production instance) mirroring the existing `client: Optional[...] = None`
  test-injection pattern, and passes `host=endpoint.host` plus the resolver
  into each `Upstream(...)` it constructs. `context.build_context` grows a
  matching `resolver: Optional[HostResolver] = None` passthrough to
  `UpstreamRouter`, alongside its existing `client` parameter.
* `core/guard.py`, `Guard.handle`: wrap `response = await self.forward(...)`
  in `try/except ResolutionError`. On catch: replace `decision` with
  `Decision(False, f"dns resolution failed for host {intent.host!r}")`,
  compute `response = self.deny_response(intent, decision, view)`, and
  `upstream_status = None` — falling through to the *same* `self.audit.log`
  call already at the end of `handle`, unconditionally. This is the one
  place a resolution failure becomes both a denial and an audited event; no
  other exception type is caught here, so a genuine upstream network error
  keeps behaving exactly as it does today (uncaught, no audit line — that
  gap is pre-existing and out of scope for this doc).
* Note for the orchestrator to confirm: `record()` already runs before
  `forward()` in the kernel pipeline (durability contract: "a crash never
  loses a write"), so a receive-pack push whose resolution fails after
  `record()` has already counted the ref against branch/write quotas even
  though the client sees a deny. This matches the existing contract (the
  write is never silently lost, retried, or duplicated) but is worth a
  one-line confirmation since it is new failure surface for that contract.

## Test-suite fallout (fix in this same commit)

Real DNS resolution now sits inside `UpstreamRouter`, which today is built
directly (no resolver argument) at roughly two dozen call sites across ten
existing test files — left alone, every one of them would attempt a live
query for a fixture hostname (e.g. `gitlab.example`) that does not resolve.
Add one `autouse` fixture to `warden/tests/conftest.py`:

```python
@pytest.fixture(autouse=True)
def _stub_resolver(monkeypatch):
    async def _resolve(self, host: str) -> str:
        return "203.0.113.10"  # RFC 5737 TEST-NET-3, never a routable answer
    monkeypatch.setattr(DnsResolver, "resolve", _resolve)
```

This patches the class method, so every `UpstreamRouter(cfg)` /
`build_context(...)` built anywhere in the suite — regardless of call site —
resolves to the same fixed test IP with zero changes to the ~25 existing
construction call sites. A test that needs different behavior (a failing
resolution, or a distinguishable IP) calls `monkeypatch.setattr` again
locally, inside that test, overriding the autouse default for its own scope.

## Tests

New `warden/tests/core/test_transport_resolution.py` (unit, injected fake
resolver implementing `HostResolver` directly — no real DNS, no real TLS):

* `Upstream.git_get` / `open_rest` / `git_post_stream` build a request whose
  URL host is the resolved IP and whose `extensions["sni_hostname"]` and
  `Host` header both equal the real endpoint host — assert against the
  captured `httpx.Request` via a `respx` route (or the existing
  `httpx.MockTransport` pattern already used in `test_git_proxy.py`).
* A resolver that raises `ResolutionError` makes every `Upstream` method
  raise `ResolutionError` before any request reaches the mock
  transport — assert zero calls recorded on the mock. This is the "never
  connects to its own alias" guarantee: no request escapes with the
  unresolved hostname as a literal connection target either.

Extend an existing end-to-end guard test (`test_git_proxy.py` and
`test_api_proxy.py`, one case each): with a resolver stubbed to raise
`ResolutionError` for the configured endpoint host, a request through
`GitGuard`/`ApiGuard` returns a deny response, and exactly one
`audit.jsonl` line is written with `decision.allow == False` and the
resolution-failure reason — the named fail-closed test from the main doc's
§5.
