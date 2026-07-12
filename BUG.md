# Bug: Warden cannot reach its own git host — `gitlab.com` DNS alias self-loops

**Component:** catraz 0.1.0 — compose network generation (`catraz/compose.py`, `render_hosts_fragment`)
**Severity:** High — the git/API path is completely non-functional; no agent GitLab
operation can succeed.
**Observed:** every reconcile fails with `All connection attempts failed`; the
warden logs `initial reconcile incomplete — state stays locked` and stays
fail-closed for the whole session.

```
2026-07-12 06:53:40 ERROR warden: git reconcile failed for untis-org/.../webuntis-images@gitlab.com: All connection attempts failed
2026-07-12 06:53:40 ERROR warden: api reconcile failed for untis-org/.../wvp-opt-api@gitlab.com: All connection attempts failed
2026-07-12 06:53:40 ERROR warden: initial reconcile incomplete — state stays locked
```

## What catraz did wrong

The warden plays two roles for the same name, `gitlab.com`, and catraz wired
them so the two collide:

1. **Server side (correct).** So the agent's `gitlab.com` traffic is
   transparently routed through the warden, catraz generates
   `.catraz/compose.hosts.yml` giving the warden a **network alias** `gitlab.com`
   on the internal `agent-net`:

   ```yaml
   services:
     gitlab-warden:
       networks:
         agent-net:
           aliases:
             - gitlab.com
   ```

2. **Client side (broken).** The warden itself must connect *outbound* to the
   **real** gitlab.com (via `egress-net`) to reconcile and proxy. Its upstream
   base URL is built straight from the endpoint host — `https://gitlab.com` /
   `https://gitlab.com/api/v4` (`warden/core/transport.py: base_urls`).

Docker's embedded DNS (`127.0.0.11`) serves a network alias to **every**
container attached to that network — **including the container that owns the
alias**. Because the warden is itself on `agent-net` and owns the `gitlab.com`
alias there, its own lookup of `gitlab.com` resolves to **its own agent-net IP**,
not to the real GitLab. Every outbound upstream call therefore dials the
warden's own address on port 443, where nothing listens, and fails.

In other words: catraz put the alias on the warden's own interface and never
gave the warden a way to resolve the *real* host, so the warden's DNS view of
`gitlab.com` points back at itself. The mistake is not the alias — it's that
the alias is allowed to shadow the warden's own upstream resolution, with no
override pinning the real host.

## Evidence

Inside the affected warden container:

```
# gitlab.com resolves to the warden's OWN agent-net IP, not the real host
$ python3 -c "import socket; print(socket.getaddrinfo('gitlab.com',443)[0][4])"
('172.28.0.2', 443)          # <- warden's own agent-net address

# /etc/hosts confirms 172.28.0.2 is this container
127.0.0.1   localhost
172.28.0.2  1f7bf2258495     # <- the warden itself

# egress works in general (not a connectivity problem)
$ python3 -c "import socket; socket.create_connection(('1.1.1.1',443),5)"   # OK

# the REAL gitlab.com is reachable when the name is bypassed:
# connect to the real IP with SNI/Host gitlab.com -> 401 (reachable, needs token)
$ ... connect 172.65.251.78:443, server_hostname=gitlab.com ...
HTTP/1.1 401 Unauthorized
```

So the host is reachable and egress is fine; the **only** failure is that the
warden resolves `gitlab.com` to itself.

## Scope

Not project-specific. Any catraz stack routing a `[[git.endpoint]]` host through
the warden hits this. Confirmed on a second, unrelated stack on the same host,
whose warden likewise resolved `gitlab.com` to its own IP (`192.168.16.2`). It
reproduces for **every** configured git host, self-hosted GitLab included — the
alias mechanism is host-agnostic.

## Why it isn't caught earlier

- The warden's healthcheck only probes the admin Unix socket
  (`socket.AF_UNIX ... admin.sock`), which is up regardless of upstream
  reachability — so the container reports **healthy** while every reconcile
  fails.
- Reconcile failure is fail-closed by design (state stays locked), so the
  symptom is "all GitLab ops denied", which looks like a policy/credential
  problem rather than a DNS wiring bug.

## Fix applied locally (workaround)

Pin the real upstream IP into the warden's `/etc/hosts` via the sanctioned
project override (`.catraz/compose.override.yml`, layered last by
`compose.py: _source_cmd`). `/etc/hosts` (glibc `files`) is consulted before
Docker DNS, so the warden's upstream lookup hits the real host while the
agent-facing alias is untouched:

```yaml
services:
  gitlab-warden:
    extra_hosts:
      - "gitlab.com:172.65.251.78"   # gitlab.com Cloudflare anycast IPv4
```

This is a workaround, not a proper fix: it hardcodes a Cloudflare IP that can
rotate (refresh with `getent ahostsv4 gitlab.com`).

## Suggested proper fix (in catraz itself)

The warden must resolve the git-endpoint host to the **real** upstream, not to
its own alias. Options, roughly in order of preference:

1. **Don't overload the name.** Give the warden a distinct upstream target
   instead of reusing `gitlab.com`. Have `base_urls` connect to a dedicated
   upstream hostname (or the resolved real IP) with `Host`/SNI still set to
   `gitlab.com`, so the agent-facing alias and the warden's upstream never share
   a resolvable name.
2. **Pin the real IP at generation time.** When `render_hosts_fragment` emits
   the `gitlab.com` alias on the warden, also emit an `extra_hosts` entry on the
   warden mapping each endpoint host to its resolved public IP (re-resolved on
   each `up`/`reload`), which shadows the self-alias for the warden's own
   lookups. This is the automated form of the workaround above.
3. **Move the alias off the warden's own resolution path** — e.g. terminate the
   agent-facing `gitlab.com` on a separate proxy endpoint/interface that the
   warden does not itself resolve through, so owning the alias no longer poisons
   the warden's upstream DNS.

Additionally, the warden healthcheck should verify upstream reachability (or at
least surface reconcile-locked state), so this fails loudly at `up` time instead
of silently reporting `healthy` while every git op is denied.
