# 03 — GitLab guard: `guards/git/gitlab/` on recognizers

Derives from main document §2.2–§2.4, §3.1–§3.3, §4, §6. Depends on: 01, 02.
Parallel with `03-transport-guard.md` — in shared wiring files (`app.py`,
`context.py`) touch **only this guard's** import/registration lines.

## Goal

The REST guard becomes recognizer-based under `guards/git/gitlab/`, with the
full recognizer catalog from the vocabulary table — including the newly named
endpoints — and without any special-cased read table, builtin deny, or
activation machinery.

## Move / create

* Move `guards/gitlab_api/` → `guards/git/gitlab/`; the `catalog/` subpackage
  dissolves into the guard package.
* `gitlab/actions.py`: `SUPPORTED` = all `project.*` and `instance.*` actions
  plus `repo.read` and `repo.branch.create` (shared, transport-independent
  actions). Startup subset check against the namespace vocabulary.
* `gitlab/recognizers.py` — `RestRecognizer(Recognizer[ApiIntent])` matching
  on method + path template (keep `path_template`/regex matching and the
  scope-policy payload: `namespace_field`, iid-lookup marker, quota kind —
  rename `EndpointKind` → `QuotaKind` here). Catalog rows, most specific
  first:

  **Writes**
  - `POST …/merge_requests` → `project.mr.create`
  - `POST …/merge_requests/{iid}/notes|discussions|discussions/{id}/notes` → `project.mr.comment`
  - `PUT …/merge_requests/{iid}` → field-conditional: no `state_event` →
    `project.mr.edit`; `close`/`reopen` → `project.mr.close`; `merge` →
    `project.mr.merge`; any other value → `frozenset()` (deny)
  - `PUT …/merge_requests/{iid}/merge` → `project.mr.merge`
  - `POST …/pipeline`, `POST …/merge_requests/{iid}/pipelines`,
    `POST …/pipelines/{id}/retry|cancel`,
    `POST …/jobs/{id}/retry|cancel|play` → `project.ci.trigger`
  - `POST …/repository/branches` → `repo.branch.create`
  - `POST …/issues` → `project.issue.create`;
    `PUT …/issues/{iid}` field-conditional like the MR row →
    `project.issue.edit` / `project.issue.close`;
    `POST …/issues/{iid}/notes` → `project.issue.comment`

  **Reads** (project-bound)
  - `GET/HEAD …/repository/{rest}` → `repo.read`
  - `GET …/jobs/{id}/artifacts{/rest}` → `repo.read` (artifact downloads are
    content); job logs (`…/jobs/{id}/trace`) stay under the project blanket
  - `GET …/snippets{/rest}` (project-bound) → `repo.read`
  - catch-all `GET/HEAD /projects/{id}{/rest}` → `project.read` (last row;
    MR diffs/changes/versions land here deliberately)

  **Reads** (projectless)
  - `/projects`, `/groups{...}` trees, global `/merge_requests`, `/issues` →
    `instance.projects.read`
  - `/users`, `/user{/rest}`, `/events` → `instance.users.read`
  - `/version`, `/metadata`, `/broadcast_messages` → `instance.meta.read`
  - `/search` + `/groups/{id}/search`: scope-field mapping
    `projects|merge_requests|issues|milestones → instance.projects.read`,
    `users → instance.users.read`, anything else → `frozenset()` (deny)

## Delete

* `catalog/activation.py` (`EffectiveTable`, `EMPTY_TABLE`,
  `build_effective_table`), `catalog/builtin.py`, `catalog/read_endpoints.py`,
  `catalog/write_endpoints.py`, the old `actions.py`
  (`ACTION_TO_RECOGNIZERS`, `DEFAULT_ACTIONS`, `actions_valid_for_type`, all
  consistency asserts), `ReadClass`, `ClassifyFn`,
  `ScopeKind.CONTENT_EXPOSURE`, and every `_BRIDGE_10_03` symbol from step 02.
* The separate `GraphqlGuard` class: fold `/api/graphql*` routes into this
  guard as routes that recognize nothing → uniform deny (keep the explicit
  deny reason: unmodelled channel).

## Change

* `guard.py`: match against the **full** catalog (`first_match`); the
  effective-actions check is set membership per host — **interim** inside the
  existing `capability_gate` hook (kernel takes over in step 04, which also
  moves it before `enrich`). The MR iid→namespace lookup in `enrich` keys off
  the matched recognizer's scope payload, as today.
* `policy.py`: `decide_scope` keeps exactly two scope kinds
  (branch-namespace, quota-by-kind); R1/R2/R3/R5/R6 semantics unchanged.
  Quota: MR quota applies to `project.mr.create` rows (`QuotaKind.MR`).
* Audit fields include recognized action ids; the non-default-enable marker is
  derived from `DEFAULT` membership instead of `enabled_via` bookkeeping.
* Keep `report.py` compiling against the new catalog minimally (full
  multi-guard report is step 05).

## Constraints

* Fail-closed field handling: only declared decision fields are extracted,
  from their declared location; unknown `state_event`/`scope` values recognize
  to nothing.
* Never widen: everything the old catalog denied stays denied; the *only*
  intentional widenings are the ones named in main document §3.3 and §6.

## Tests (rework `tests/catalog/*`, `test_api_proxy.py`, `test_policy.py`; add `tests/gitlab/test_recognizers.py`)

* Every catalog row: match + recognized action set (table-driven).
* `state_event` matrix: absent/close/reopen/merge/unknown → edit/close/merge
  (class-denied)/deny.
* Search scope matrix incl. unknown scope → deny.
* Content line: `/repository/*`, artifacts, snippets → `repo.read`; MR diffs
  and job trace → `project.read`; with `project.read` enabled but `repo.read`
  disabled, file reads and artifact downloads are denied while MR diffs pass.
* GraphQL still denied on every method/path.
* Merge denied by criticality on both wire shapes even with every
  configurable action enabled.

## Verification

Warden commands from `00-index.md` step 4.

## Commit

```
warden: rewrite gitlab guard onto recognizers
```

Identity/docstring rules: see `00-index.md` — strip design-doc references and
history notes from every docstring touched by the move. Flip Status in
`00-index.md` in the same commit.
