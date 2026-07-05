# 06 — Red-team & e2e coverage, leftover sweep, doc cross-references

Derives from main document §2.2, §3.1–§3.3, §6, §8. Depends on: 01–05.

## Goal

Prove the fail-closed edges of the new model end-to-end, remove every remnant
of the old one, and mark superseded doctrine in the older design documents.

## Red-team tests (`warden/tests/redteam/test_bypass.py`, extend)

Each as a request through the full pipeline (`full_decide` or app client),
asserting deny + rule id:

* `state_event` smuggling: unknown value, casing variants, value in query
  instead of body (declared location must win), `state_event=merge` on the
  plain MR `PUT`.
* Search scope fuzz: unknown scope, missing scope, `blobs`/`commits`/
  `wiki_blobs` (content scopes) — all denied.
* Content line: file read via `/repository/files`, artifact download, and a
  project snippet each denied when `repo.read` is disabled while
  `project.read` is enabled; MR diff still passes in that config.
* Push batch: one forbidden ref (tag create / branch delete) poisons the whole
  push; N branch-creates against a quota of N−1 rejected.
* Old ids in config (`git.push`, `mr.update`) → `ConfigError` at startup.
* GraphQL on every method; MR pipeline trigger with `project.ci.trigger`
  disabled; pipeline `DELETE` (unmodelled → denied).

## E2E / container

* Extend the container test (pattern of 09's step 05): a review-only endpoint
  (`repo.read`, `project.read`, `project.mr.comment`) can clone, read MR
  diffs, comment — and is denied push, MR create, file read via
  API-with-repo.read-off scenario, merge.
* `test_git_e2e.py`: full push flow on new ids incl. per-ref deny message
  naming the action.

## Sweep

* Grep the whole repo (warden + src/catraz + docs) for deleted symbols and old
  ids: `ACTION_TO_RECOGNIZERS`, `DEFAULT_ENABLED`, `actions_valid_for_type`,
  `EffectiveTable`, `build_effective_table`, `is_builtin_merge_endpoint`,
  `ReadClass`, `ClassifyFn`, `CONTENT_EXPOSURE`, `Capability`, `FORBIDDEN`,
  `forbidden_check`, `EndpointKind`, `gitlab_api`, `git.fetch`, `git.push`,
  `mr.update`, `_BRIDGE_10_03`, `TODO(10-04)`. Hits only in historical design
  docs (07–09) are acceptable; hits in code, tests, or the 10-docs are not.
* Docstring pass over every module touched by this rework, enforcing the
  rules in `00-index.md` (no design-doc references, no history, no Sphinx
  roles, shortest-possible).

## Doc cross-references

* `08-multi-target.md` §3.1 doctrine and `09-endpoint-actions.md`: add a
  short superseded-note at the top pointing to `10-actions-rework.md`
  (specifically: the invariant read table, the `git.fetch`/`git.push`
  transport verbs, and the eight-action vocabulary are superseded). Do not
  rewrite their content — they stay as history.

## Verification

Both command sets from `00-index.md` step 4 (warden + CLI/container).

## Commit

```
warden: red-team and e2e coverage for the actions rework, final sweep
```

Identity/docstring rules: see `00-index.md`. Flip Status in `00-index.md` in
the same commit — with this step the index shows all seven rows ✅.
