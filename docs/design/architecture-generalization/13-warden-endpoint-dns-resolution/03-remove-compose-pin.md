# 03 — remove-compose-pin

Derives from [`../13-warden-endpoint-dns-resolution.md`](../13-warden-endpoint-dns-resolution.md)
§2. On contradiction the main doc wins — report it, do not guess. Requires
[02-connect-via-resolved-ip](02-connect-via-resolved-ip.md) landed and green:
the pin is removed only once the warden resolves its own upstream live.
Catraz-only; no warden changes.

## How

With the warden resolving each endpoint host at connect time, the host-side
`extra_hosts` pin in `src/catraz/compose.py` is redundant.

* Delete `_resolve_host_ipv4` — the host-side snapshot that existed solely to
  feed the pin.
* `render_hosts_fragment(hosts: list[str]) -> str` drops the `host_ips`
  parameter and the `extra_hosts:` block entirely; it emits only the
  `agent-net` `aliases:` list and the `no_proxy`/`NO_PROXY` lines. The
  `agent-net` alias itself stays untouched — it is what routes the *agent's*
  host-named traffic to the warden; only the warden's own self-directed pin
  goes.
* `write_hosts_fragment` drops the `host_ips = {...}` comprehension and
  calls `render_hosts_fragment(hosts)` with the one remaining argument.
* Docstrings on both functions updated to drop the pin description
  (greenfield — no "used to pin" / "no longer" framing).
* No compose network-topology change needed: `gitlab-warden` already carries
  `networks: [agent-net, egress-net]` in `docker-compose.yml`, and
  `egress-net` is unrestricted (no allowlist gate on the warden's own
  traffic, unlike the agent's proxy-gated path) — the DNS path to the
  configured nameserver needs no compose or squid edit. State this in the
  step rather than adding a diff.

## Tests

`tests/cli/test_compose.py`:

* Delete `test_render_hosts_fragment_pins_resolved_ip_on_warden` and
  `test_render_hosts_fragment_no_pin_without_resolved_ip` (the pin they
  test no longer exists).
* Update `test_write_hosts_fragment_writes_from_warden_toml`: drop the
  `monkeypatch.setattr(compose, "_resolve_host_ipv4", ...)` line and the
  `"gitlab.com:203.0.113.7"` pin assertion; keep the alias assertion.
* `test_render_hosts_fragment_lists_every_host_as_alias`,
  `..._no_proxy_includes_every_host_plus_loopback`, and
  `..._empty_hosts_is_valid_shape` keep passing unchanged — confirmation
  that only the pin path was touched.
* Add a regression test asserting `"extra_hosts"` never appears in any
  rendered fragment, across every hosts list shape tried (empty, one host,
  multiple) — catches a future change that accidentally reintroduces the
  pin.

## Before release (outside this plan's scope)

A live smoke test — `catraz up --remote` against a real endpoint host, then
a real clone and a prefixed push, confirming the warden reaches upstream
with the pin gone — is not exercisable in this sandbox (the sandbox's own
resolver is exactly the aliasing DNS this work routes around) and is not
covered by any step here. It gates a release, not this plan.
