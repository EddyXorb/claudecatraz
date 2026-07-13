# 13 — Warden upstream DNS for aliased endpoints — implementation steps

These derive from the main document
[`../13-warden-endpoint-dns-resolution.md`](../13-warden-endpoint-dns-resolution.md)
(the *what/why*); each step's *how* is its own file beside this index. On
contradiction the main document wins — report it, do not guess.

## Order and progress

The number is the dependency level: same number = independent (parallelizable,
separate commit); a higher number requires all lower ones. Flip the Status
column in the same commit as the step.

| Level | Step | Status |
| --- | --- | --- |
| 01 | [controlled-resolver](01-controlled-resolver.md) | ☐ |
| 02 | [connect-via-resolved-ip](02-connect-via-resolved-ip.md) | ☐ |
| 03 | [remove-compose-pin](03-remove-compose-pin.md) | ☐ |

Level 01 is a pure module: a resolver interface plus a production
implementation that queries an explicit nameserver, never the container's own
(aliasing) resolver — testable with a mocked query backend, no event loop
touching a real socket. Level 02 threads that resolver through the warden's
one upstream transport (`Upstream`/`UpstreamRouter`) so every request targets
the resolved IP while TLS SNI, the `Host` header, and certificate validation
stay pinned to the real hostname; the fail-closed deny+audit path lands in
the same commit, since a resolution mechanism without an audited denial path
is not a shippable intermediate state. Level 03 deletes the compose
`extra_hosts` pin and `_resolve_host_ipv4` — sequenced last, once level 02 is
green, so the warden is never left unable to reach upstream.

## Verification (every step)

```
cd warden && uv run --extra dev pytest -q
cd warden && ruff check . && mypy warden
```

Level 03 additionally touches the catraz operator surface:

```
uv run --with pytest python -m pytest tests/cli -q
uv run mypy
```

Live DNS/TLS behavior is not reproducible in this sandbox — the sandbox's own
resolver is exactly the aliasing DNS this work routes around. Every test here
is unit-level against a mocked or stubbed resolver: a resolution failure
denies and is audited, and the outgoing request targets the resolved IP,
never the warden's own alias. A live smoke test (`catraz up --remote` against
a real endpoint host, then a real clone and push) is a release gate outside
this plan's scope, not something these steps can exercise.

## Hard rules

Identity **EddyXorb**, no AI/tool mentions anywhere. One commit per step,
green each time. The security-relevant test lands in the same commit as the
change it covers: a resolution failure denies and is audited, and the
warden never connects to its own alias. Docstrings/comments per
`docs/RULES.md` (≤5/≤2 lines, no `docs/design` or `§` references in code
prose). The override is scoped to endpoint hosts only — the warden's shared
`httpx.AsyncClient` is already dedicated exclusively to endpoint upstream
traffic (git Smart-HTTP + REST), so nothing else the warden touches needs a
separate carve-out.

## Flagged for the orchestrator

* **(a) Resolver mechanism / new dependency.** Recommended: `dnspython`
  (`dns.asyncresolver.Resolver(configure=False)`, `nameservers` set to one
  explicit configured address) — a small, focused, well-audited library, and
  `configure=False` guarantees `/etc/resolv.conf` (Docker's aliasing
  resolver) is never consulted, not even as a fallback. Alternative
  (no new dependency): a hand-rolled UDP DNS-query encoder/decoder — more
  code, more edge cases (truncation, EDNS, retry/backoff) to get right in a
  security-relevant path; not recommended. Confirm the dependency is
  acceptable or name a preferred alternative.
* **Resolver address**: a new `DNS_RESOLVER` env var (infra-class config,
  same unprefixed family as `AGENT_PORT`/`ADMIN_PORT`/`STATE_DB_PATH` —
  env-only, no `warden.toml` tunable), defaulting to `1.1.1.1`.
  `squid.conf` already carries a commented-out precedent for this exact
  address (`# dns_nameservers 1.1.1.1 8.8.8.8`). `egress-net` (where the
  warden already sits) is unrestricted — no allowlist gate on the warden's
  own traffic — so the DNS path needs no compose/squid change.
* **(b) Connect-to-resolved-IP mechanism, and confidence it validates TLS
  correctly.** `httpx.AsyncHTTPTransport.__init__` (pinned httpx 0.28.1) has
  no `network_backend` passthrough to `httpcore.AsyncConnectionPool` — a
  custom-network-backend design is not reachable through public httpx API
  without reimplementing transport internals, and would additionally be
  invisible to `respx` (which replaces the transport wholesale, the
  mechanism the entire existing test suite relies on for upstream mocking).
  The verified mechanism instead operates at the `httpx.Request` level,
  confirmed by reading the pinned `httpcore==1.0.9` connect path directly:
  swap the request URL's authority to the resolved IP
  (`httpx.URL(url).copy_with(host=ip)`), pass `extensions={"sni_hostname":
  real_host}`, and set an explicit `Host` header to `real_host`. In
  `httpcore/_async/connection.py`, `connect_tcp` is called with the URL's
  (now IP) host, and TLS's `server_hostname` is `sni_hostname or
  origin.host` — so the certificate is validated against the real hostname
  regardless of which IP the socket connects to. In `httpx/_models.py`,
  `Request._prepare` only auto-derives the `Host` header `if not has_host`,
  so an explicit `Host` header always wins over the swapped URL's netloc.
  High confidence — verified against the exact pinned source in this repo's
  venv, not against general httpx documentation.
* **No caching.** Each upstream call re-resolves at connect time, matching
  the main doc's "resolves...at connection time" wording; no TTL cache is
  introduced, since caching would reintroduce the staleness the pin exists
  to fix. Flag if the extra per-request DNS round trip is unwanted.
* **(c) Verification is mock-based only.** Live DNS/TLS is not verifiable in
  this sandbox (see Verification above); the fail-closed and
  connects-to-resolved-IP tests are unit-level with an injected fake
  resolver.
* **Test-suite blast radius.** Level 02 introduces a real DNS dependency into
  `Upstream`/`UpstreamRouter`, which today are constructed directly (no
  resolver argument) at roughly two dozen call sites across ten existing
  test files (`conftest.py`, `test_api_mr_namespace.py`, `test_api_proxy.py`,
  `test_api_reconcile.py`, `test_app.py`, `test_git_e2e.py`,
  `test_git_proxy.py`, `test_git_reconcile.py`, `test_host_routing.py`,
  `test_reconcile_all.py`, `test_report.py`). Left unhandled, every one of
  those tests would attempt a real DNS query against a fixture hostname
  (e.g. `gitlab.example`) that does not resolve. The step avoids rewriting
  every call site by adding one `autouse` stub fixture in `conftest.py`
  instead; see that step for the mechanism.
