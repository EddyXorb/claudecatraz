# 13 — Warden upstream DNS for aliased endpoints

Every routable git host is a `[[git.endpoint]]` (08→10), and the compose layer
gives the warden that host's alias on `agent-net` so the agent's host-named
traffic (git-over-HTTPS and REST) lands on the warden transparently, without the
agent ever rewriting the host to a service name. Docker's embedded DNS serves
that alias to **every** container on `agent-net` — including the warden itself —
so the warden's own upstream calls to the real host resolve back to the warden
and loop.

The shipped fix pins the host's real IP into the warden's `/etc/hosts` (compose
`extra_hosts`, resolved on the host machine, which is not on `agent-net` and so
sees the true address). glibc consults `files` before `dns`, so only the
warden's own lookups are redirected to the real host; the agent still resolves
the alias to the warden. This document records the **limit** of that pin and the
clean successor. It is *what/why* only; there is no implementation scheduled.

> Prerequisite: 08, 09, 10, 11 implemented (the endpoint model and the compose
> alias/pin in `compose.render_hosts_fragment` / `write_hosts_fragment`). This is
> a flagged follow-up, not part of the init/doctor cleanup.

---

## 1. What the pin does and where it stops

`write_hosts_fragment` re-resolves each endpoint host on **every**
`up`/`run`/`shell`/`down` and rewrites the pin, so a fresh container always
starts with a fresh address. But `extra_hosts` writes `/etc/hosts` at container
**creation**, and that entry is fixed for the container's whole lifetime — the
warden never re-resolves it while running.

The staleness window is therefore exactly one case: a **long-lived** warden
container against a host whose upstream IP **rotates during that lifetime**.

* **`catraz run` (one-off)** — the container lives minutes; each run re-resolves.
  No practical staleness.
* **Self-hosted git on a fixed IP** — the address never moves; the pin is exact
  and ideal.
* **`catraz up --remote` against `gitlab.com`** — `gitlab.com` sits behind an
  anycast front whose IP is stable most of the time but not guaranteed. A daemon
  running for days can outlive its pinned address; upstream calls then fail until
  the container is recreated. This is the only case the pin does not cover well.

The pin is also correct precisely *because* it is host-resolved: the container
cannot resolve the true address itself (its DNS is the aliasing Docker resolver),
so the pin must come from outside `agent-net`. The consequence is that it is a
snapshot, not a live lookup.

## 2. The target — the warden resolves its own upstream

The warden is the one component that opens the upstream connection, so it is the
right place to resolve the endpoint host — dynamically, at connect time, past the
aliasing resolver. It reaches the internet on `egress-net`, so it can query an
explicit upstream resolver directly instead of the container's default (which
would loop through the alias).

* For each configured `[[git.endpoint]]` host, the warden resolves the address at
  connection time via a resolver it controls, not `/etc/hosts` and not Docker's
  embedded DNS. It connects to the resolved IP while keeping the real hostname as
  SNI and `Host`, so TLS validates against the host's certificate and an anycast
  address is fine.
* The override is scoped to endpoint hosts only. Every other name the warden
  might touch keeps the container's normal resolution; nothing else is aliased,
  so nothing else needs the bypass.
* Fail closed: if the controlled resolver cannot answer, the request is denied,
  not retried through the default resolver (which would resolve the host to the
  warden itself and connect to a port it does not serve). A resolution failure is
  a hard error surfaced in the audit trail, never a silent fallback.

With the warden resolving live, the compose `extra_hosts` pin is redundant and is
removed; the `agent-net` alias (which routes the *agent* to the warden) stays.
`render_hosts_fragment` then emits only the alias and `no_proxy` entries, never a
pinned IP, and `_resolve_host_ipv4` — a host-side snapshot that exists solely to
feed the pin — is deleted with it.

## 3. Alternatives considered

* **Keep the static pin (today).** Simplest, zero runtime code, exact for
  fixed-IP hosts. Rejected as the end state only for the long-lived-daemon +
  rotating-host case; kept until this document is implemented.
* **Periodic `/etc/hosts` refresher in the warden entrypoint.** Stays in
  compose/entrypoint scope but adds a background writer that races in-flight
  lookups and is only as fresh as its poll interval — a coarser version of §2
  with more moving parts. Rejected.
* **Drop the alias; route the agent to the warden by service name.** Removes the
  self-loop at the source, but breaks the deliberate transparency property — the
  agent uses the *real* host name everywhere, exactly as it would outside the
  container — and enlarges the agent-facing surface. Rejected.

## 4. Scope note

This is warden-runtime work: the resolution lives where the upstream connection
is made (the REST client and the git smart-HTTP proxy in `guards/gitlab`), not in
the catraz operator surface. It is independent of 12 (per-host scoping) and can
land before or after it. Until it lands, the pin from 11 remains the shipped
behaviour and is sufficient for one-off runs and fixed-IP deployments.

## 5. Conventions for the implementation

Author and committer are the repo identity **EddyXorb**; no co-authorship or
"generated with" trailers, no mention of AI tools anywhere. One commit per step,
each leaving the warden suite green (`cd warden && uv run --extra dev pytest -q`,
`ruff check .`, `mypy warden`) plus the catraz surface where the pin removal
touches it (`uv run --with pytest python -m pytest tests/cli -q`, `uv run mypy`).
The security-relevant test lands with the change: a resolution failure denies and
is audited, and the warden never connects to its own alias. Docstrings and
comments follow `docs/RULES.md`: short, no history, no obvious statements, no
markup, and no references to files under `docs/design/`.
