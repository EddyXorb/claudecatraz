# 05 — `/policy` report over all guards; catraz template, `init`, `doctor`

Derives from main document §3, §5, §6. Depends on: 01–04.

## Goal

Introspection and the operator-facing surface speak the new language: the
report covers every guard uniformly (git rows finally appear), and the
shipped `warden.toml` documents the twenty-action vocabulary.

## Change (warden)

* Rewrite the policy report (`report.py`, admin `/policy` route): one section
  per configured host; per section, walk **every** guard of the host's
  endpoint type through the shared contract — for each recognizer: id,
  recognized-action ids, criticality, quota kind where present, whether
  default, whether active for this host. Never-class actions appear as named
  denials (replaces the hardcoded `builtin_deny: ["mr.merge"]`).
* Audit: every logged event carries the recognized action ids (both guards
  already emit them since level 03 — verify the envelope is uniform).

## Change (catraz, `src/catraz/`)

* `src/catraz/assets/config/warden.toml`: rewrite the `actions` documentation
  block — the vocabulary table (id, meaning, criticality class, default
  yes/no/never), the three scopes, the repo/project content line incl. the MR
  diff and artifact carve-outs, and the review-only endpoint example using
  `["repo.read", "project.read", "project.mr.comment"]`. State that the
  built-in default also lives in code and that a missing key ≠ empty list.
* `catraz init` / `catraz doctor`: new ids in generated config and in
  coherence warnings (e.g. a write action enabled while the host has no write
  token stays a doctor warning, not an error). Doctor mentions never-class
  actions as compiled-in, non-configurable.
* Sweep `src/catraz/` and user-facing docs for old action ids
  (`git.fetch`, `git.push`, `mr.update`, …) — none may survive.

## Constraints

* The vocabulary table in the toml comment block must match main document §3
  exactly — it is operator documentation, not a second source of truth (the
  code default rules).

## Tests

* Warden: report shape — git transport rows present; never-class rows marked;
  per-host activation differs when two hosts have different `actions`.
* CLI (`tests/cli/`): `init` output parses and validates; `doctor` warns on
  write-action/no-write-token mismatch with new ids; template ids all in the
  vocabulary (table-driven against `guards.git.actions`).

## Verification

Both command sets from `00-index.md` step 4 (warden + CLI).

## Commit

```
warden+catraz: policy report and operator surface on new action vocabulary
```

Identity/docstring rules: see `00-index.md`. Flip Status in `00-index.md` in
the same commit.
