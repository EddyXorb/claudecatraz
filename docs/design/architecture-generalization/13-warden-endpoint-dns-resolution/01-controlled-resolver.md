# 01 — controlled-resolver

Derives from [`../13-warden-endpoint-dns-resolution.md`](../13-warden-endpoint-dns-resolution.md)
§2. On contradiction the main doc wins — report it, do not guess. Pure
resolver unit, no transport wiring yet: everything here is testable with a
mocked query, no real socket.

## How

* **New module** `warden/warden/core/resolver.py`.
* `class ResolutionError(Exception)` — the one exception the controlled
  resolver ever raises for a failed lookup. Step 02 is the only other place
  that names it (it catches this exact type; nothing else is caught the
  same way).
* `class HostResolver(Protocol)` — one method,
  `async def resolve(self, host: str) -> str`, returning a single IPv4/IPv6
  literal or raising `ResolutionError`. No return-`None` path: absence of an
  answer is always the exception, never a falsy value a caller could
  silently ignore.
* `class DnsResolver` — the production implementation.
  `dns.asyncresolver.Resolver(configure=False)` with `.nameservers` set to
  `[address]` (one explicit upstream nameserver) — `configure=False` is
  load-bearing: it skips reading `/etc/resolv.conf` entirely, so Docker's
  aliasing resolver is never consulted, not even as an unintended fallback.
  `resolve` calls `.resolve(host, "A")`, returns the first answer's
  `.address`; `dns.resolver.NXDOMAIN`, `dns.resolver.NoAnswer`,
  `dns.exception.Timeout`, and the base `dns.exception.DNSException` all
  become `ResolutionError` — never propagate as a bare dnspython exception
  past this module.
* **New dependency**: `dnspython` added to `dependencies` in
  `warden/pyproject.toml` (flagged in `00-index.md`).
* **Config**: add `dns_resolver: str = "1.1.1.1"` to `Config` in
  `core/config.py`; wire the `DNS_RESOLVER` env var in `config_load.py`'s
  `from_env` (plain `env.get`, same infra-config family as `AGENT_PORT` /
  `ADMIN_PORT` / `STATE_DB_PATH` — env-only, no `warden.toml` tunable, no
  validation beyond "non-empty string" since a malformed address simply
  fails every resolution and is caught by the fail-closed path in step 02).
* Scope: `DnsResolver` is only ever asked to resolve configured endpoint
  hosts (`UpstreamRouter` is the sole caller, wired in step 02). Nothing
  here changes process-wide resolution.

## Tests

New `warden/tests/core/test_resolver.py`, all against a mocked
`dns.asyncresolver.Resolver` (monkeypatched or injected) — no real network:

* a successful answer returns the address string;
* `NXDOMAIN`, `NoAnswer`, and a timeout each raise `ResolutionError` (three
  separate cases, not folded into one);
* `DnsResolver` is constructed with `configure=False` and
  `nameservers == [configured address]` — asserted directly on the
  constructed resolver object, so a future edit that drops `configure=False`
  (and silently reintroduces `/etc/resolv.conf`) fails this test.

Extend `warden/tests/test_config.py` (or add
`test_config_load_dns_resolver.py`): `DNS_RESOLVER` env var overrides the
default; an absent env var yields `"1.1.1.1"`.
