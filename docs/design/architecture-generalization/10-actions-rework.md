# 10 — Actions Rework: one language for everything the warden permits

Today the warden's permission model is split across several mechanisms that grew
separately: an action vocabulary that physically lives inside the GitLab guard,
a side table mapping actions to recognizers (kept consistent by import-time
asserts), a git guard with no recognizer concept at all, a parallel
"capability" layer catching dangerous sub-cases the coarse actions cannot
express, a special-cased invariant read table, and a special-cased built-in
merge deny. This document replaces all of that with **one** model.

> **The whole model in one sentence:** Every request maps, via recognizers, to
> a set of actions; it proceeds iff every action is below IRREVERSIBLE in
> criticality and enabled for the target host; everything else is denied.

Reads, writes, git transport, REST, and every future guard namespace obey that
sentence without exception.

Prerequisite: 08-multi-target and 09-endpoint-actions are fully implemented.
This is a pre-1.0 rework: the old action ids and the `[api.endpoints]`-era
machinery are removed without a compatibility layer.

> **For humans vs. for agents.** This document is the *what/why*. The
> step-by-step implementation lives in the subfolder
> [`10-actions-rework/`](10-actions-rework/00-index.md) and derives from this
> document; on contradiction, this document wins — report the contradiction
> instead of guessing.

---

## 1. What is wrong today

* **No registry.** `guards/gitlab_api/actions.py` owns the whole vocabulary;
  the git guard defines two bare string constants that the GitLab guard
  imports; `core/config.py` deferred-imports guard modules to do generic
  cascade work. Core depends on a guard, one guard depends on the other.
* **Two sources of truth, glued by asserts.** `ACTION_TO_RECOGNIZERS` is a
  side table reconciled against `WRITE_ENDPOINTS` by an import-time
  consistency check; `DEFAULT_ACTIONS` is reconciled against `DEFAULT_ENABLED`
  by a second assert. Editing one table and forgetting the other is only
  caught at import time, not by construction.
* **Asymmetric guards.** The GitLab guard has recognizers, an effective table,
  and activation machinery; the git guard has an `if/elif` ladder
  (`action_for_git_operation`). The `/policy` report walks only the REST
  catalog, so `git.fetch`/`git.push` appear as names without rows.
* **Wire-derived vocabulary.** `mr.update` names a GitLab HTTP endpoint, not
  an operation. Because one endpoint can perform several operations of very
  different criticality (a `PUT` on an MR edits a title *or* merges it via
  `state_event=merge`; one `git push` can update, create, and delete branches
  and tags in a single batch), a parallel capability layer
  (`core/capabilities.py`, `FORBIDDEN`, per-guard mapping functions) exists
  solely to compensate for the coarseness of the actions.
* **Special cases.** The projectless read table has its own row semantics
  (`ReadClass`, `classify` callbacks, `ScopeKind.CONTENT_EXPOSURE`); the merge
  endpoint is a hand-rolled "built-in deny" outside every table
  (`builtin.py`); GraphQL is a separate guard class.

## 2. The model

### 2.1 Actions and criticality

```python
class Criticality(IntEnum):
    READ = 0          # no state change
    WRITE = 1         # reversible state change (create, edit, comment, close)
    IRREVERSIBLE = 2  # merge, tag creation/deletion, branch deletion

@dataclass(frozen=True)
class Action:
    id: str
    criticality: Criticality
```

Both types live in `core/`. The **vocabulary** (the concrete `Action`
instances) does *not* live in core — each guard namespace defines its own.
Core treats action ids as opaque strings: it never parses them, never imports
a namespace's vocabulary, and validates only through hooks the guards provide.
(A global core enum was considered and rejected: it would couple core to every
future namespace's vocabulary, e.g. a Postgres guard's `table.drop`.)

`IRREVERSIBLE` is the compiled-in deny class: actions of this criticality
exist in the vocabulary so that denials are *named* (auditable, reportable)
but can never be enabled by configuration. The check is one line in the
kernel — the single place irreversible operations are refused. This replaces
the entire capability layer: `core/capabilities.py` (the `Capability` enum,
`FORBIDDEN`, `forbidden_check`), the per-guard mapping functions
(`git_ref_capabilities`, `api_capabilities`), and `builtin.py` (the merge
endpoint becomes an ordinary recognizer mapping to an ordinary action that its
criticality class denies) are all deleted.

### 2.2 Recognizers

A recognizer answers "what would this request do" as a set of actions:

```python
class Recognizer(ABC, Generic[IntentT]):
    id: str
    def matches(self, intent: IntentT) -> bool: ...
    def recognize(self, intent: IntentT) -> frozenset[Action]: ...
```

The contract lives in `core/`; each guard subclasses it with its own match key
as plain data — the GitLab guard matches on HTTP method + path template, the
transport guard on the git smart-HTTP operation and, for receive-pack, on the
parsed ref-commands. There is no shared match-key struct, only the shared
contract: the *kinds* of matching are genuinely different, the *pipeline*
around them is identical.

`recognize` returns a **set** because one request can perform several
operations: a single `git push` batch yields e.g.
`{repo.branch.push, repo.tag.create}` — and the whole request is denied if any
member fails the gate (batch atomicity, matching today's per-push behavior).

**Field-conditional mapping with fail-closed allowlists** is the standard
pattern for endpoints whose meaning depends on request fields, replacing the
old per-row `classify` callbacks:

* `PUT …/merge_requests/{iid}` with no `state_event` → `project.mr.edit`;
  `state_event ∈ {close, reopen}` → `project.mr.close`;
  `state_event = merge` → `project.mr.merge` (denied by class); any *other*
  value → **no action → denied**. A future GitLab field value never rides in
  silently.
* `GET /search?scope=…` maps each known metadata scope to the corresponding
  `instance.*.read` action; an unknown scope maps to nothing and is denied.

A request that matches no recognizer, or whose recognizer yields no action,
is denied. There is no other allow path.

### 2.3 The kernel pipeline

```
parse → recognize → kernel gates → enrich → decide → record → forward
                    ├ host allowlist
                    ├ mode gate (writes)
                    ├ project allowlist
                    ├ criticality gate   (any action ≥ IRREVERSIBLE → deny)
                    └ action gate        (every action ∈ effective actions of host)
```

The action gates run in the kernel, **before** `enrich`: a request whose
action is disabled never triggers credential-backed lookups (today the
MR-namespace lookup runs before the endpoint-activation check can deny). The
`capability_gate` hook on the Guard ABC is removed; guards no longer supply
any per-guard invariant checks to the kernel.

### 2.4 Orthogonality

Actions say **what**. Everything else stays a separate axis, deliberately not
encoded in action ids:

* **Scope policy** — branch namespace (R2), project allowlist (R6): *where*.
  A push outside `branch_prefixes` is still `repo.branch.push`; R2 denies it.
* **Quotas** (R5): *how much*. Quota categories are renamed `QuotaKind`
  (the name `EndpointKind` is freed; "endpoint" from now on means exactly one
  thing: a configured host + type).
* **Tokens / access mode**: *which credential the wire needs*. Example: push
  discovery (`GET info/refs?service=git-receive-pack`) reads refs — its action
  is `repo.read` — but the upstream still requires the write token; that is
  transport plumbing, not permission language. Consequence: with pushing
  disabled, the denial happens at `receive-pack` with a precise per-ref reason
  ("action repo.branch.push not enabled") instead of a blunt 403 at discovery.

## 3. The vocabulary (git namespace)

Grammar: `noun[.noun].verb` where the noun chain is the scope path. Plural
nouns mark instance-wide listing/discovery intent. The grammar is
**documentation only** — the machine treats ids as opaque members of a flat
closed set: no wildcards, no inheritance, no read/write split semantics, no
`actions_add`/`actions_remove`. (A read-subsumption rule was considered and
became obsolete when per-sub-object read actions were dropped.)

Three scopes with distinct enforcement boundaries:

* **`repo`** — the version-controlled content itself, *independent of wire*
  (git transport and REST reach the same bytes).
* **`project`** — the forge objects around it (MRs, CI, issues) in an
  allowlisted project.
* **`instance`** — projectless, forge-wide metadata.

| Action | Crit. | Covers | Default |
|---|---|---|---|
| `repo.read` | READ | clone/fetch + push discovery; `/repository/*` (files, trees, commits, archive, compare); CI artifact downloads; project snippets | ✔ |
| `repo.branch.create` | WRITE | new branch — git push or REST | ✔ |
| `repo.branch.push` | WRITE | move tip of existing branch | ✔ |
| `repo.branch.delete` | IRREV. | — | never |
| `repo.tag.create` | IRREV. | — | never |
| `repo.tag.delete` | IRREV. | — | never |
| `project.read` | READ | all forge-object reads in an allowlisted project: attributes, MRs **incl. diffs**, issues, pipelines/jobs **incl. logs**, labels, milestones, releases | ✔ |
| `project.mr.create` | WRITE | open MR | ✔ |
| `project.mr.edit` | WRITE | title/description/labels (no `state_event`) | ✔ |
| `project.mr.close` | WRITE | `state_event ∈ {close, reopen}` | ✔ |
| `project.mr.comment` | WRITE | notes, discussions, replies | ✔ |
| `project.mr.merge` | IRREV. | `/merge` endpoint + `state_event=merge` alias | never |
| `project.ci.trigger` | WRITE | branch **and** MR pipelines; retry, play, cancel | ✔ |
| `project.issue.create` | WRITE | — | opt-in |
| `project.issue.edit` | WRITE | title/description/labels | opt-in |
| `project.issue.close` | WRITE | `state_event ∈ {close, reopen}` | opt-in |
| `project.issue.comment` | WRITE | issue notes | opt-in |
| `instance.projects.read` | READ | `/projects`, `/groups*`, global MR/issue listings, search scopes `{projects, merge_requests, issues, milestones}` | ✔ |
| `instance.users.read` | READ | `/users`, `/user*`, `/events`, search scope `users` | ✔ |
| `instance.meta.read` | READ | `/version`, `/metadata`, `/broadcast_messages` | ✔ |

Twenty actions, three scopes, four compiled-in nevers.

### 3.1 The repo/project content line

Enforced **mechanically by path**, never by response inspection:

* Everything under `/projects/{id}/repository/*` → `repo.read`.
* CI **artifact downloads** → `repo.read` (artifacts are files, often the
  built code). CI job **logs** stay `project.read` (babysitting pipelines
  without repo access is a real review-only workflow).
* Project **snippets** → `repo.read` (content by class, though not git data).
* **MR diff views** (`…/merge_requests/{iid}/diffs|changes|versions`) →
  `project.read`. This is a deliberate, documented carve-out: MR diffs are
  code excerpts of the changed files; the leak is the price of
  "review without clone".

`project.read` is the only project-scoped read action — there are no
per-sub-object read actions. Without it, nothing in the project is visible.

### 3.2 Instance scope

* Groups fold into `instance.projects.read` (groups are project containers,
  same discovery purpose). Global `/merge_requests` and `/issues` listings
  fold in too — all cross-project *metadata* discovery, one knob.
* `instance.meta.read` exists because `/version`, `/metadata`, and
  `/broadcast_messages` have no project or user dimension to fold into, and
  API tooling probes `/version` on handshake — denying it breaks clients
  confusingly. (`instance.read` was rejected as a name: it reads like a
  blanket over the whole instance scope, which it is not.)
* True unification of instance listings with the project allowlist would
  require filtering response bodies (parse paginated JSON, drop rows, rewrite
  pagination) — a trust-surface jump from decide-and-forward to
  content-rewriting, with fail-open risk. Rejected for now; noted as a
  possible future opt-in hardening layer.

### 3.3 Newly named surface

The vocabulary now leads and the recognizer catalog follows. Previously
*unmodelled* (i.e. accidentally default-denied) endpoints that become named:

* MR pipelines (`POST …/merge_requests/{iid}/pipelines`) — under
  `project.ci.trigger`, alongside retry/play/cancel of pipelines and jobs.
* Issue edit/close/comment — the `project.issue.*` family (opt-in),
  symmetric with `project.mr.*`.
* `DELETE /pipelines/{id}` destroys logs permanently and stays outside the
  vocabulary → default-denied (nameable later as a never-class action).
* GraphQL stays deny-all (an unmodelled channel that could express every
  blocked write), implemented inside the gitlab guard instead of a separate
  guard class.

## 4. Module layout

```
warden/
  core/
    actions.py       Action, Criticality — the types, never a vocabulary
    recognizer.py    Recognizer contract + generic first-match helper
    endpoints.py     EndpointType: a type name + its guard composition
    guard.py         Guard ABC + kernel pipeline (incl. both action gates)
  guards/
    git/
      actions.py     the git-namespace vocabulary (table above) + DEFAULT
      endpoints.py   ENDPOINT_TYPES: "plain" = (transport,)
                                     "gitlab" = (transport, gitlab)
      transport/     git smart-HTTP guard — serves EVERY git-namespace type
        actions.py   SUPPORTED = frozenset({repo.read, repo.branch.*, repo.tag.*})
        …            guard.py, recognizers.py, policy.py, pktline.py, state.py
      gitlab/        GitLab REST guard (GraphQL deny-all folded in)
        actions.py   SUPPORTED = frozenset({project.*, instance.*, repo.read,
                                            repo.branch.create})
        …            guard.py, recognizers.py, policy.py, state.py
    db/              future namespace: own actions.py vocabulary, own guards —
                     zero core changes
```

Naming and dependency decisions:

* **`transport`, not `plain`.** "plain" is an endpoint *type* — a composition
  that happens to contain only the transport guard. GitLab endpoints use the
  transport guard too; renaming it "plain" would bake in a false claim.
* **An endpoint type is a composition of guards.** The per-type valid action
  set is *derived* — the union of `SUPPORTED` over the type's guards — which
  deletes the hardcoded `actions_valid_for_type` ladder. "A plain endpoint has
  no forge vocabulary" becomes true by construction.
* **`SUPPORTED`, not `ALLOWED`.** What a guard *can* enforce (static) and what
  an operator *permits* (config-effective) must not share a word. `SUPPORTED`
  is always accessed qualified (`gitlab.actions.SUPPORTED`), never imported
  unqualified — enforceable with ruff's banned-api if wanted. Each guard's
  `SUPPORTED` must be a subset of the namespace vocabulary, checked at
  startup.
* **No namespace descriptor object.** There is no `Domain` class and no
  `common/` subpackage: the namespace *is* the package; its vocabulary is
  `guards/git/actions.py`, its type registry `guards/git/endpoints.py`.
* **Actions are transport-independent.** `repo.branch.create` covers the git
  ref-command *and* `POST …/repository/branches`; `repo.read` covers
  clone/fetch *and* REST file reads. One toml entry governs all wires that
  produce the same effect — both guards list the shared action in their
  `SUPPORTED`.
* **Recognizer tables live in `recognizers.py`** per guard (the former
  `write_endpoints.py`/`read_endpoints.py` split dies with the special-cased
  read semantics).

## 5. Configuration

The cascade per host is unchanged in shape, revocabularied in content:
per-endpoint `actions` override → `[git].actions` namespace default →
built-in `DEFAULT`. Rules:

* A missing `actions` key is **not** an empty list — absent means "inherit".
  Lists **replace completely**; there is no add/remove syntax.
* An **explicit** per-endpoint list containing an action outside its type's
  derived set → `ConfigError`, startup aborts. An **inherited** namespace
  default is silently intersected with the type's set (otherwise adding one
  plain endpoint would forbid using the namespace default at all).
* Unknown action id anywhere → `ConfigError`, startup aborts. Fail-closed.
* Effective action sets are built **once at startup** — no runtime reload, no
  drift. Per host, the effective set is a plain `frozenset[str]`; the old
  `EffectiveTable`/`EMPTY_TABLE` per-host row tables are deleted (matching
  runs against the full catalog; authorization is set membership).

```toml
[git]
# namespace default — omit entirely to get the built-in DEFAULT
actions = ["repo.read", "repo.branch.create", "repo.branch.push",
           "project.read", "project.mr.create", "project.mr.edit",
           "project.mr.close", "project.mr.comment", "project.ci.trigger",
           "instance.projects.read", "instance.users.read", "instance.meta.read"]

# review-only endpoint: can read and comment, nothing else
[[git.endpoint]]
host    = "review-only.example.com"
type    = "gitlab"
actions = ["repo.read", "project.read", "project.mr.comment"]
```

## 6. Behavioral changes vs. today

* `repo.branch.create` is default-on and covers both wires (the REST
  `branch.create` was opt-in; branch creation via push was implicitly allowed
  — one switch now governs both).
* Push discovery is allowed under `repo.read`; a disabled push is denied at
  `receive-pack` with per-ref reasons instead of at discovery.
* Projectless reads are ordinary configurable actions now. The old doctrine
  "the read table is invariant and not action-addressable" is **superseded**;
  `ReadClass`, `ClassifyFn`, and `ScopeKind.CONTENT_EXPOSURE` are deleted.
* All old action ids (`git.fetch`, `git.push`, `mr.create`, `mr.comment`,
  `mr.update`, `pipeline.trigger`, `branch.create`, `issue.create`) are gone;
  templates and docs are rewritten. No compatibility shim (pre-1.0).
* MR pipelines and pipeline/job retry/play/cancel become allowed under
  `project.ci.trigger` (previously accidental default-denies). The issue
  write family becomes available opt-in.
* The `/policy` report walks every guard's catalog through the shared
  contract — git rows finally appear, and never-class actions are listed as
  named denials instead of a hardcoded `builtin_deny` string.

## 7. Rejected alternatives (and why)

* **Global `Action` enum in core** — couples core to every namespace's
  vocabulary; core must stay opaque-string territory.
* **Per-action capability wiring without splitting the actions** — cannot
  express request-dependent danger (a push that deletes, an MR edit that
  merges); fine-grained actions make the parallel layer unnecessary instead.
* **Criticality as CRUD verbs (read/add/delete)** — misclassifies the two
  most important denies (merge and tag creation are "adds"); the axis is
  reversibility.
* **Response-body filtering for instance listings** — different trust
  surface (content-rewriting proxy), fail-open risk; future hardening at most.
* **Mapping push discovery to a push action** — names the tool, not the
  effect; also yields worse error messages than the per-ref receive-pack deny.
* **Read subsumption down the scope tree** — obsolete once per-sub-object
  read actions were dropped; the vocabulary stays flat and inheritance-free.

## 8. Conventions for the implementation

**Docstrings.** New and edited docstrings must:

* never reference files under `docs/design/` — the code must explain itself;
* contain no historical notes (no "formerly", no "was called X", no
  design-doc section numbers, no commit references);
* never state the obvious (`get_branch`: "gets the branch");
* be as short as possible: only the interesting, potentially surprising facts
  someone needs to grasp how the thing works;
* use no Sphinx cross-reference roles (`:class:`, `:func:`, `:mod:`,
  `:data:`) and no markup-heavy formatting — plain prose that reads well
  without an IDE.

**Commits.** One commit per implementation step, authored and committed as
the repo identity **EddyXorb** (already configured locally for this repo — do
not add trailers). No co-authorship lines, no "Generated with" lines, no
mention of AI assistants or tools anywhere: not in commit messages, not in
branch names, not in MR/PR titles or descriptions.

**Tests.** Every step ships the tests its step file names; behavior must be
proven by tests, not by inspection. The full warden verification
(`uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`,
`uv run mypy`) must pass at the end of every step — intermediate commits are
never red.
