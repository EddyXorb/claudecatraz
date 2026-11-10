# 15 — GitHub REST guard: `type = "github"` with the same action language

`type = "github"` is reserved today and rejected at startup. This document makes
it a real endpoint type: git Smart-HTTP through the existing transport guard plus
a GitHub REST guard that speaks the **same action vocabulary** as the GitLab
guard. An operator configures a GitHub host exactly like a GitLab host —
`allowed_projects`, `rules`, `actions` — and the same action id means the same
effect on both forges.

> Prerequisite: 08–12 implemented. This is an intentional widening (a new
> endpoint type); nothing about `gitlab`/`plain` endpoints is widened.

---

## 1. Findings in the current code this builds on

Verified by probe tests before any change:

1. **A `plain` endpoint with `allowed_projects` locks every guard.** Both
   reconcilers iterate `cfg.open_hosts` regardless of type: the transport
   reconcile lists branches over the *GitLab* REST API and the REST guard
   resolves numeric project ids — against a host with no REST base. The
   assertion is caught by the reconcile loop, the guard stays fail-safe-locked,
   and **every write on every host** is denied forever. Fail-closed, but a
   denial of service by configuration.
2. **REST guards serve hosts of any type.** `GET /api/v4/projects/x/repository/tree`
   on a `plain` host is recognized as `repo.read` (valid for `plain`), passes
   every gate and crashes in `Upstream.rest_url` (HTTP 500). For a `github`
   host the same request would be forwarded to GitHub with a `PRIVATE-TOKEN`.
3. **The transport guard is not forge-neutral.** Its reconcile depends on the
   GitLab REST surface although its docstring says forge-agnostic; that is the
   root of (1).
4. **`core/path_template.py` is dead** (0 % coverage) while the GitLab
   recognizers carry their own, richer `_compile`.
5. **Dot segments escape the decided endpoint.** Templates match `..` as a
   segment and httpx collapses it before sending: `PUT /projects/x/issues/..`
   is decided as an issue edit and arrives as a project-settings edit;
   `GET /projects/x/../other%2Fp/…` reads a project outside the allowlist.
6. **Decision fields are read from the body only, but GitLab merges query
   parameters into its params.** `PUT …/merge_requests/7?state_event=merge`
   recognizes as `project.mr.edit` (DEFAULT) and is forwarded with the write
   token. A redteam test asserts this forwarding as intended. Not changed here
   — see §6.
7. **The open-MR counter only moves at reconcile.** Nothing records a created
   MR, so between reconciles `max_open_mrs` is bounded only by the hourly rate
   limit; reopening (`project.mr.close`) never checks the ceiling at all.

(1)–(5) are fixed here (§4); (6) and (7) are GitLab-only behaviour changes
proposed in §6.

## 2. What GitHub changes, and how the model absorbs it

| Concern | GitLab | GitHub | Decision |
| --- | --- | --- | --- |
| REST base | `https://H/api/v4` | `https://api.github.com` (github.com), `https://H/api/v3` (GHES) | Agent always calls `http://H:8080/api/v3/…`; the type's upstream layout maps it. |
| REST auth | `PRIVATE-TOKEN` | `Authorization: Bearer` | Per-type upstream layout, injected by the composition root; core stays forge-free. |
| git auth | Basic `oauth2:<t>` | Basic `x-access-token:<t>` | Same layout. |
| Project id | `/projects/{url-encoded path}` or numeric id | `/repos/{owner}/{repo}`; numeric `/repositories/{id}` | `/repositories/*` is not recognized (deny) — no alias resolution needed. |
| Merge request | MR, `iid` | Pull request, `number` | Same `project.mr.*` actions. |
| PR state change | `state_event=close/reopen/merge` | `PATCH state=closed/open`; merge is its own endpoint | `state` absent → `edit`, `closed`/`open` → `close`, else empty set (deny). |
| PR ↔ issue | disjoint | **every PR is an issue**: `/issues/{n}` and `/issues/{n}/comments` act on PRs too | Resolved per request (§3.2): same effect ⇒ same action id. |
| Reviews | (not modelled) | `pulls/{n}/reviews`, review comments, replies | `event` absent/`COMMENT` → `project.mr.comment`; `APPROVE`/`REQUEST_CHANGES` → empty set (deny). |
| CI | pipelines, jobs | Actions: workflow dispatch (`ref`), run rerun/cancel, job rerun | Same `project.ci.trigger`; dispatch is branch-namespace-checked on `ref`. |
| Branch create via REST | `repository/branches` (`branch`) | `git/refs` (`ref=refs/heads/…`) | `refs/heads/` → `repo.branch.create`; `refs/tags/` → `repo.tag.create` (irreversible). `DELETE git/refs/…` → branch/tag delete (irreversible). |
| Pagination | `X-Next-Page` | `Link: rel="next"` | Guard-local paginator. |
| GraphQL | `/api/graphql` | `/graphql` | Always denied (unmodelled channel). |

Fork safety: a PR is the agent's only if its **head ref** is in the branch
namespace **and** its head repository is the base repository itself. A fork PR
whose branch happens to be called `claude/x` is someone else's. `head` on
create may be `owner:branch`; only the base repository's own owner is accepted.

## 3. The guard

### 3.1 Surface

* Routes: `/api/v3/{rest:path}`. `/api/v3/graphql` is denied with the GraphQL
  reason. GitLab's `/api/graphql` routes stay with the GitLab guard.
* Every REST guard serves only hosts whose endpoint type composes it; any other
  host is denied before anything else (§1 finding 2). This is a kernel gate fed
  by a guard hook, so core still never names a forge.
* Audit `guard` value: `"github"`.

### 3.2 PR-or-issue resolution

`/issues/{n}` (PATCH) and `/issues/{n}/comments` (POST) recognize to PR actions
when `n` is a pull request and to issue actions otherwise. The kernel needs the
action set before its gates, so the guard resolves the kind during `parse`, but
only after the same preconditions the kernel would check (host served and open
for writes, project allowed). The kind is identified positively with one
read-token `GET /issues/{n}`: a `pull_request` key → PR, none → issue, any
failure (including 404, which may hide a PR the token cannot see) → unknown →
empty action set → deny. Without this, closing a PR through the issues endpoint
would bypass both `project.mr.close` and the branch-namespace check.

### 3.2a Wire strictness

GitHub reads every write body as JSON whatever its `Content-Type`, and merges
query parameters into it. So for a write: a query string, a body that is not
one JSON object, a duplicated key, or a decision field that is not a string all
yield the empty action set. Rows whose body keys matter carry a complete key
allowlist (PR create: no `issue`, no `head_repo`; review: no stray keys).
Forwarded request headers are an allowlist (`Accept`, `Content-Type`,
`User-Agent`, `X-GitHub-Api-Version`, conditional-request headers). Every
template placeholder for a number matches digits only, and a dot segment never
matches any row (both REST guards).

### 3.2b Whose object is it

A pull request is the agent's iff its head ref is in the branch namespace and
`head.repo.id == base.repo.id` (a fork's same-named branch, or a deleted fork,
is not). A workflow run likewise (`head_branch`, `head_repository.id ==
repository.id`); a job is the agent's iff its run is. Rerun/cancel are scoped
this way — unlike GitLab's retry/cancel rows, which are quota-only (§6).
Reopening a PR (`state=open`) checks the open-MR ceiling like a create.

### 3.3 Catalog (paths below `/api/v3`)

Writes: `pulls` create · `pulls/{n}` update · `pulls/{n}/merge` (irreversible) ·
`pulls/{n}/comments` · `pulls/{n}/comments/{id}/replies` · `pulls/{n}/reviews` ·
`pulls/{n}/reviews/{id}/events` · `issues` create · `issues/{n}` update ·
`issues/{n}/comments` · `actions/workflows/{id}/dispatches` ·
`actions/runs/{id}/{rerun,rerun-failed-jobs,cancel}` · `actions/jobs/{id}/rerun` ·
`git/refs` create · `git/refs/{ref}` delete.

Reads: repository content (`contents`, `git`, `commits`, `branches`, `tags`,
`compare`, `tarball`, `zipball`, `readme`, `actions/artifacts`) → `repo.read`;
every other `/repos/{o}/{r}/…` GET → `project.read`; `/search/{repositories,
issues,users}`, `/repositories`, `/orgs…`, `/organizations`, `/issues` →
`instance.projects.read` / `instance.users.read` (`/user…`, `/users…`,
`/events`); `/meta`, `/rate_limit`, `/versions` → `instance.meta.read`.
Everything else — including `PATCH /repos/{o}/{r}/git/refs/…`, the contents
write API, `/merges`, `/dispatches` and `/repositories/{id}/…` — is unrecognized
and denied.

### 3.4 State

The open-MR counter table is shared across forges (keyed by host, which already
separates them) and moves from the GitLab guard to the git namespace. The
GitHub reconcile rebuilds it from `GET /repos/{o}/{r}/pulls?state=open`,
counting only fork-safe agent PRs.

## 4. Steps

| Level | Step |
| --- | --- |
| 01 | Type-scoped guards: a host-served gate; each reconcile iterates only hosts of a composing type; transport reconcile lists branches via the forge-neutral ref advertisement. |
| 01 | Upstream layouts per type, injected into `UpstreamRouter`; forge-specific pagination leaves core. |
| 02 | One path-template compiler (with digit-only placeholders and the dot-segment rule) for both REST guards; the open-MR table moves to the git namespace. |
| 03 | GitHub guard: actions, intent, catalog, PR lookup, policy, reconcile, routes; `type = "github"` accepted. |
| 04 | Operator surface: config README, agent instructions (`/api/v3`). |

## 5. Known limits

* GitHub Actions run workflow code from the agent's branch with the secrets the
  repository exposes; the warden cannot see inside a run. Operators keep
  secrets behind protected environments, default `GITHUB_TOKEN` to read-only,
  and give the write token no workflow-file permission. The same holds for
  GitLab pipelines.
* A bare `ref` in a workflow dispatch resolves to a tag first if a tag of that
  name exists; the agent cannot create tags, so only a pre-existing human tag
  in the namespace could be hit.
* `tarball`/`zipball`/artifact downloads answer with a redirect to a signed
  storage URL the agent fetches through the forward proxy.

* `gh` uses GraphQL for most porcelain commands (`gh pr create`, `gh pr view`);
  through the warden only `gh api` / plain REST works.
* Reaching `api.github.com` by its own name is not aliased; the agent uses the
  endpoint host (`github.com:8080/api/v3`).

## 6. Proposed GitLab follow-ups (not part of this change)

* Deny every GitLab write that carries a query string, or read decision fields
  from the query as well and deny on conflict (finding 6) — and flip the
  redteam test that asserts the forwarding.
* Count an allowed MR create towards `open_mrs` until the next reconcile, and
  give reopen the MR ceiling (finding 7).
* Scope `pipeline.retry`/`pipeline.cancel`/`job.*` to pipelines of agent
  branches, like the GitHub run lookup.
* Rename the GitLab guard's audit/lock name from `"api"` to `"gitlab"` now that
  a second REST guard exists.
