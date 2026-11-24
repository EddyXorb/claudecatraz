# 16 — Instant config apply

Editing `.catraz/config/warden.toml`, the egress allowlist, or a git token
should take effect within seconds, without the operator remembering
`catraz reload`. Only changes that alter a container's *shape* (image, compose
interpolation from `.env`, network aliases) still need a recreate — and catraz
detects those precisely instead of guessing from mtimes.

> Prerequisite: 08–14 implemented (endpoint model, grouped token files,
> compose-rendered host aliases, allowlist blocks).

---

## 1. Where config reaches each container today

| Input (host, under `.catraz/`) | Reaches | How it is mounted | Read when |
| --- | --- | --- | --- |
| `config/warden.toml` | warden | single-file bind `:ro` | once, `from_env()` at process start |
| `secrets/read_tokens`, `secrets/write_tokens` | warden | compose `secrets:` (single-file bind) | once, `from_env()` at process start |
| `config/squid.conf`, `config/allowlist.txt` | forward-proxy | single-file binds `:ro` | squid start (or `squid -k reconfigure`, never wired up) |
| `config/warden.toml` `[[git.endpoint]].host` | warden network aliases + `extra_hosts`, agent `no_proxy` | rendered into `compose.hosts.yml` | container creation |
| `.env` | every service (compose interpolation) | — | container creation |
| image sources | every service | — | build |

Consequences:

1. **Every policy edit needs a container recreate.** The warden builds `Config`
   once; squid is PID 1 and nothing ever reconfigures it.
2. **`catraz reload` misses secrets.** Its staleness map lists only
   `warden.toml` + `.env` for the warden, so a new token is invisible to it —
   the user's only remedy is `reload --force`.
3. **Single-file bind mounts pin an inode.** An editor that saves by
   write-to-temp + rename leaves the container looking at the *old* file
   forever; even an in-container reload would not see the edit.
4. **Staleness is guessed from mtime vs. container start**, which says nothing
   about what the running process actually enforces.

## 2. Constraint: config stays immutable per process

`docs/RULES.md` binds: *effective config is built once at startup — no runtime
reload*. The rule exists because a mutable `Config` shared by the guards, the
router, the quota state and in-flight requests is a race surface on the trust
boundary: a half-swapped policy, a guard holding a stale reference, a request
judged against two policies. This design keeps that property exactly — every
warden *process* still sees one `Config` for its whole life — and applies a
change by **replacing the process**, not mutating it. RULES.md is clarified to
say "per process" so the two cannot be read as conflicting.

## 3. Design

### 3.1 Supervisors, one per infra container

Each infra container gets a tiny supervisor as PID 1 that owns the real service
process and watches that service's inputs:

* **warden** — `python -m warden supervise`. Polls a content fingerprint of
  `warden.toml`, `read_tokens`, `write_tokens` (default every 1 s). On a change
  that is **stable across two polls** (debounce: a save is never seen half
  written), it validates the new inputs with the very same `from_env()` the
  server uses, then:
  * **valid** → SIGTERM the running warden (bounded wait, then SIGKILL), start a
    fresh `python -m warden` child. The child reconciles before opening its
    port exactly as on a cold start.
  * **invalid** → stop the running warden and start nothing (**deny-all**),
    log the `ConfigError` at ERROR, record it in the status file, keep watching.
    The next valid save brings the warden back on its own.
  A child that exits with the config-abort code (2) puts the supervisor into
  the same deny-all state; any other exit ends the supervisor with that code, so
  compose's `restart: on-failure` semantics stay as they are.
* **forward-proxy** — `squid_supervise.py`, the same state machine as the
  warden's, standalone because the image does not carry the warden package.
  Same fingerprint/debounce loop over `squid.conf` + `allowlist.txt`;
  validates with `squid -k parse`; valid → `SIGHUP` (squid's native reconfigure:
  listeners and ACL files are re-read, established tunnels are kept); invalid →
  stop squid (**no egress**), record, keep watching; a later valid save starts
  squid again.

Only one warden process ever runs at a time: the old one has fully exited
before the new one opens the SQLite state and the admin socket.

### 3.2 Directory mounts instead of file mounts

`.catraz/config/` is mounted as a **directory** (`:ro`) into the warden
(`/etc/warden/config`, `WARDEN_CONFIG_PATH` points into it) and the proxy
(`/etc/catraz/config`; the image symlinks `/etc/squid/squid.conf` and
`/etc/squid/allowlist.txt` there, so existing `squid.conf` copies keep their
paths). A directory bind follows renames, so editor saves are seen.

The token files stay compose `secrets:` (single-file). Mounting
`.catraz/secrets/` as a directory would also hand the warden the Anthropic
key and Claude credentials — a widening for convenience. catraz itself rewrites
token files in place (same inode), so `catraz init` edits are seen; a
rename-save by an editor is caught by the fingerprint check below and reported
as out of sync.

### 3.3 Status file = what is actually enforced

Each supervisor writes a three-line status file after every decision:

```
state=applied|rejected
fingerprint=<hex>
detail=<one line: empty, or the rejection reason>
```

* warden → `/run/warden/config-status` (host: `.catraz/state/warden/run/`)
* proxy → `/var/log/squid/config-status` (host: `.catraz/logs/squid/`)

Neither directory is visible to the agent (`/workspace/.catraz` is a tmpfs
shadow in the agent container). The fingerprint is
`sha256( concat( sha256(file_i) or "-" , "\n" ) )` over the service's inputs in
a fixed order; catraz computes the same value from the host files. That turns
"is the running service current?" from an mtime guess into an exact comparison:

| host fingerprint vs. status | meaning | catraz reaction |
| --- | --- | --- |
| equal, `applied` | running service enforces the file on disk | nothing to do |
| equal, `rejected` | service is in deny-all for the file on disk | loud error with reason; a recreate would not help |
| differs, inputs changed < 10 s ago | supervisor has not picked it up yet | "pending" |
| differs, older | container cannot see the file (pinned inode, old image) | out of sync → `catraz reload` recreates |
| no status file | image predates the supervisor | mtime fallback, as before |

### 3.4 What still needs a recreate, and how catraz knows

`catraz reload` keeps its job for shape changes and becomes exact:

* `.env` newer than the container start → recreate (compose interpolation).
* warden: the `[[git.endpoint]]` host list differs from the aliases in
  `compose.hosts.yml` → recreate warden (network alias, `extra_hosts`) and tell
  the user the agent needs a restart for its `no_proxy`. Until then the new host
  is unreachable for the agent — closed, not open.
* config inputs: only when the status file says out of sync (or is missing and
  the mtime fallback fires). The secrets are part of the warden's inputs.
* `catraz status` shows a *Config* section per service; `catraz run` prints a
  rejected config loudly before starting the agent; `catraz allow` waits a few
  seconds for the warden to confirm the new allowlist.

Infra `up` in `catraz run` passes `--build` (as the agent and the remote
daemon already do) so a CLI upgrade never pairs the new mounts with an image
that lacks the supervisor.

## 4. Per-setting summary

| Setting | Mechanism | Latency | Invalid new value |
| --- | --- | --- | --- |
| `warden.toml` `[git]` actions, `[git.rules]`, endpoint `allowed_projects` / `rules` / `actions` / `type` | warden supervisor: validate → restart child | 1–2 s detect + warden startup (reconcile against upstream, typically < 2 s) | warden stopped → every agent git/REST call refused (deny-all); `ERROR` log, status `rejected`, `catraz status`/`run` show the reason |
| `[[git.endpoint]]` add/remove host | policy part as above; alias/`no_proxy` need `catraz reload` + agent restart | instant (policy) / manual (routing) | as above; an unrouted host is unreachable |
| `secrets/read_tokens`, `write_tokens` | warden supervisor (part of the fingerprint) | as warden.toml | malformed line / duplicate host → deny-all as above; a missing token closes only that host (unchanged fail-closed degrade) |
| `allowlist.txt` | proxy supervisor: `squid -k parse` → SIGHUP | 1–2 s, reconfigure < 1 s | squid stopped → no egress; status `rejected` with squid's last message |
| `squid.conf` | as allowlist | as allowlist | as allowlist |
| `.env` | `catraz reload` (recreate) | manual | compose interpolation errors surface at `up` |
| image / Dockerfile / catraz upgrade | `catraz reload --force` / `--build` on `run` | manual | — |

During a warden restart its port is closed for the startup window; the agent
sees connection refused — a deny, never a bypass (the agent has no other route
to the git hosts). In-flight requests get the same bounded grace as a container
stop (10 s).

## 5. Fail-closed reasoning

* **Never more permissive than the file on disk.** At every instant the agent
  faces either a process built from the current, validated inputs, a process
  built from the previous validated inputs during the ≤ 2 s detection window,
  or nothing.
* **Why deny-all and not "keep the old one".** The edit that fails validation
  may be a *tightening* — a revoked token, a removed project. Keeping the old
  process would keep the agent on the policy the operator just tried to
  withdraw. Deny-all matches what a cold start with the same file already does
  (the warden refuses to start) and makes the error impossible to miss.
* **Half-written files.** The two-poll debounce means a file is only applied
  after it has been byte-identical for a full poll interval; catraz's own writes
  finish in microseconds.
* **The agent cannot trigger a reload.** All inputs live under `.catraz/`,
  which the agent sees only as an empty tmpfs; its `/etc/catraz/warden.toml`
  view is read-only.
* **Quotas survive restarts.** Write counts live in the SQLite state; the new
  process reconciles open MRs/branches before serving.

## 6. Alternatives considered

* **In-process reload** (watch file, rebuild `Config`, swap `AppContext`).
  Lowest latency (no reconcile, no port gap) but it breaks the
  immutable-config rule and needs every guard, the router and the state to
  tolerate a swap mid-request. The process restart costs about a second and
  keeps the invariant structural. Rejected; revisit only if the startup
  reconcile becomes slow enough to matter.
* **Keep `catraz reload`, call it from every catraz command that writes config.**
  Covers `catraz allow`/`init`, not a hand edit in the IDE — which is the
  reported pain. Also a full container recreate (seconds, briefly drops all
  egress). Kept only as the fallback for shape changes.
* **A host-side watcher daemon in catraz.** catraz is a CLI, not a daemon; a
  background process per sandbox adds lifecycle and platform (inotify on
  Docker Desktop bind mounts is unreliable) problems. The in-container poll is
  a hash of three small files per second.
* **inotify inside the container.** Not reliable across Docker Desktop's
  host-to-VM file sharing; polling is portable and cheap.
* **Mount `.catraz/secrets/` as a directory.** Fixes rename-saves of token
  files but gives the warden secrets it must not hold. Rejected (§3.2).
* **Keep the old warden running on an invalid config.** See §5.

## 7. Open points

* The agent's `/etc/catraz/warden.toml` view stays a single-file mount (it is
  advisory and the agent should not see the proxy config); after a rename-save
  it shows the old file until the agent container restarts.
* Adding an endpoint host still needs an agent restart for `no_proxy`.
* A warden child that takes longer than the healthcheck budget to reconcile is
  reported unhealthy in the meantime; nothing restarts it, it just shows.
