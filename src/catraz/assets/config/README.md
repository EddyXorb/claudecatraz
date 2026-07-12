# `config/` — host-editable configuration (mounted read-only)

This folder holds the **configurable, non-secret** files of the sandbox. It is mounted
**read-only** into the containers and is intentionally **host-editable** (e.g. in VSCode),
so policy/allowlist can be maintained without rebuilding an image.

**Core rule: NO secret ever lives here.** Tokens/secrets belong exclusively in
`.catraz/secrets/` (one file each, mode 0600, mounted as compose secrets to the authorized
service); non-secret wiring lives in `.catraz/.env`. Rationale and full picture:
`docs/design/agentic-workflow/README.md` §11.

| File | For | Applies in | Docs |
| ---- | --- | ---------- | ---- |
| `allowlist.txt` | Forward-proxy (Squid): allowed domains | stage 02 | `02-forward-proxy/03-squid-config.md` |
| `squid.conf` | Forward-proxy (Squid): filter configuration | stage 02 | `02-forward-proxy/03-squid-config.md` |
| `warden.toml` | Warden: prefix, limits, allowed projects | stage 02 | `02-warden.md` (W10) |

**Versioning:** `config/` is **deliberately committed** (it is the policy artifact). In
contrast, `.env` (non-secret wiring), `secrets/` (tokens) and `state/` / `logs/` (runtime
data) are gitignored.

**Status:** stage 01 (bootstrap hardening) lays down these files as scaffolding; they only
take effect with the stage-02 containers (Warden, forward-proxy).

---

## `warden.toml` reference

Reference for the `warden.toml` keys.

### Top-level keys (the enforced policy)

| Key | Meaning |
| --- | --- |
| `branch_prefixes` | A branch may be pushed only if its name starts with one of these prefixes (the allowed namespace is the union of the list). A single prefix may also be given as the scalar `branch_prefix = "claude/"`; set only one of the two forms. |
| `max_open_mrs` | Max simultaneously open merge requests. |
| `max_open_branches` | Max simultaneously existing agent branches. |
| `max_writes_per_hour` | Max write actions per hour. |
| `allowed_projects` | Concrete project paths (`group/sub/project`) only — **no** wildcards/globs, **no** partial/leaf names, **no** group prefixes. Empty = fail-closed (every GitLab op denied until you add one, or run `catraz allow`). This top-level list is the one the runtime actually enforces. |

### The endpoint model (`[[git.endpoint]]`)

An `[[git.endpoint]]` table is the live, enforced way to reach one git host through the
Warden: a DNS alias, the git `insteadOf` rewrite, the per-host REST base, and the
host-gate/router all key off it. Each table configures exactly one host:

```toml
[[git.endpoint]]
host = "gitlab.com"
type = "gitlab"
```

* **`type`** — implemented values: `"gitlab"` (git + REST API, gitlab.com or self-hosted)
  and `"plain"` (git smart-HTTP only, no REST API assumed). `"github"` is reserved for a
  future guard — the Warden refuses to start if it is used.
* **No endpoint at all = no host is routed or reachable** (real default-deny, not "use the
  top-level settings as one implicit host"). `catraz init` offers to add one; the shipped
  template ships none.
* **Tokens are never set here.** They live in the grouped `.catraz/secrets/read_tokens` and
  `write_tokens` files (`<host> <token>` per line). A host's access mode is derived purely
  from which of its tokens are present: a write token but no read token still runs closed
  (least privilege). An endpoint with no matching token simply runs closed for that host
  only — it never blocks startup or the other endpoints.
* **Multi-host** is a hand-edit: add one `[[git.endpoint]]` per host plus a token line each.
  The wizard does not loop hosts.

**Known limitation — per-endpoint `allowed_projects` is not yet enforced.** A per-endpoint
`allowed_projects` is parsed and validated but the runtime authorizes projects only against
the **top-level** `allowed_projects`. Set the projects at the top level; per-host project
scoping is a later change.

### Per-host overrides

* **`rules = { ... }`** — an inline table overriding a `[git.rules]` default for that host
  only (per-key merge). Always write it as an inline `{ ... }` table, **never** as a
  `[git.endpoint.rules]` header: a header binds to the positionally-last `[[git.endpoint]]`
  element, so reordering endpoint blocks silently attaches the rules to the wrong host.
* **`actions = [...]`** — replaces the domain default for that host completely (there is no
  `actions_add`/`actions_remove`). A review-only host, for example:

  ```toml
  [[git.endpoint]]
  host    = "review-only.example.com"
  type    = "gitlab"
  actions = ["repo.read", "project.read", "project.mr.comment"]
  rules   = { max_writes_per_hour = 30 }
  ```

### `[git.rules]` and `[git].actions`

`[git.rules]` holds the same domain-default knobs as the top-level keys
(`branch_prefixes`, `max_open_branches`, `max_open_mrs`, `max_writes_per_hour`,
`max_push_bytes`), scoped to the endpoint model. `[git].actions` is the domain-default
action list. Both are layered **under** the token deck: a write action with no write token
for a host still runs closed, and `catraz doctor` warns about that mismatch rather than the
Warden failing.

The built-in default (`[git].actions`, the twelve "yes" rows below) also lives in code
(`guards/git/actions.py` `DEFAULT`); the key is a documented starting point, **not** the
sole source of truth. Deleting it does not mean "no actions" — it falls back to the same
code default (a missing key is not an empty list).

### Action vocabulary (closed set of twenty ids)

Three scopes, each with its own enforcement boundary:

* **`repo`** — the version-controlled content itself, independent of wire (git transport and
  REST reach the same bytes).
* **`project`** — the forge objects around it (MRs, CI, issues) in an allowlisted project.
* **`instance`** — projectless, forge-wide metadata.

No wildcards and no read/write split beyond what is listed. `never` rows are compiled-in
denials — never configurable, regardless of what an `actions` list contains.

| action | criticality | covers | default |
| --- | --- | --- | --- |
| `repo.read` | READ | clone/fetch + push discovery; repository files/trees/commits/archive/compare; CI artifact downloads; project snippets | yes |
| `repo.branch.create` | WRITE | new branch — git push or REST | yes |
| `repo.branch.push` | WRITE | move tip of an existing branch | yes |
| `repo.branch.delete` | never | — | never |
| `repo.tag.create` | never | — | never |
| `repo.tag.delete` | never | — | never |
| `project.read` | READ | all forge-object reads in an allowlisted project: attributes, MRs incl. diffs, issues, pipelines/jobs incl. logs, labels, milestones, releases | yes |
| `project.mr.create` | WRITE | open MR | yes |
| `project.mr.edit` | WRITE | title/description/labels (no state_event) | yes |
| `project.mr.close` | WRITE | state_event = close/reopen | yes |
| `project.mr.comment` | WRITE | notes, discussions, replies | yes |
| `project.mr.merge` | never | `/merge` endpoint + state_event=merge alias | never |
| `project.ci.trigger` | WRITE | branch and MR pipelines; retry, play, cancel | yes |
| `project.issue.create` | WRITE | — | opt-in |
| `project.issue.edit` | WRITE | title/description/labels | opt-in |
| `project.issue.close` | WRITE | state_event = close/reopen | opt-in |
| `project.issue.comment` | WRITE | issue notes | opt-in |
| `instance.projects.read` | READ | `/projects`, `/groups*`, global MR/issue listings, search scopes {projects, merge_requests, issues, milestones} | yes |
| `instance.users.read` | READ | `/users`, `/user*`, `/events`, search scope users | yes |
| `instance.meta.read` | READ | `/version`, `/metadata`, `/broadcast_messages` | yes |

The `repo`/`project` content line is enforced mechanically by path, never by response
inspection: MR diff views (`…/merge_requests/{iid}/diffs|changes|versions`) are
`project.read` — reviewing a diff never needs `repo.read` — while CI artifact downloads are
`repo.read` (artifacts are files, often the built code); job *logs* stay `project.read`
(babysitting a pipeline without repo access is a real review-only workflow).
