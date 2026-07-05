# 10 Actions-Rework — implementation steps (for implementing agents)

These steps derive **exactly** from the main document
[`../10-actions-rework.md`](../10-actions-rework.md) (the *what/why*). Humans
read the main document; each file here is the *how* of one slice. On
contradiction, the main document wins — report the contradiction instead of
guessing.

**Prerequisite: 08-multi-target and 09-endpoint-actions are fully
implemented.** If a step does not find one of their foundations in place,
stop and report it instead of rebuilding it here.

## Order and progress

**The number = the dependency level.** Files with the **same** number do not
depend on each other and can be built in parallel (separate agent, separate
commit); a higher number requires all lower ones. Update the Status column
(✅ + short commit hash) **in the same commit** as the step itself.

| Level | File | Purpose | Depends on | Status |
| --- | --- | --- | --- | --- |
| 01 | [`01-core-model.md`](01-core-model.md) | core types: `Action`, `Criticality`, `Recognizer`, `EndpointType` | — | ✅ |
| 02 | [`02-vocabulary-and-config.md`](02-vocabulary-and-config.md) | git-namespace vocabulary + config cascade on new ids (with temporary bridge) | 01 | ✅ |
| 03 | [`03-transport-guard.md`](03-transport-guard.md) | rewrite git smart-HTTP guard onto recognizers (`guards/git/transport/`) | 01, 02 | ✅ |
| 03 | [`03-gitlab-guard.md`](03-gitlab-guard.md) | rewrite REST guard onto recognizers (`guards/git/gitlab/`) | 01, 02 | ✅ |
| 04 | [`04-kernel-and-assembly.md`](04-kernel-and-assembly.md) | kernel action gates, delete capability layer, derive type sets from `SUPPORTED` | 01–03 | ⬜ |
| 05 | [`05-report-and-cli.md`](05-report-and-cli.md) | `/policy` report over all guards; catraz template/`init`/`doctor` | 01–04 | ⬜ |
| 06 | [`06-redteam-e2e-cleanup.md`](06-redteam-e2e-cleanup.md) | red-team/e2e coverage, sweep for leftovers, doc cross-refs | 01–05 | ⬜ |

The two level-03 files touch shared wiring (`app.py`, `context.py`) — each may
only change the import/registration lines of **its own** guard.

## Shared workflow (identical for every step)

1. Read the referenced sections of the main document **and** the current state
   of every file the step names, before changing anything.
2. Implement.
3. **Write the tests the step names** — behavior is proven by tests, never by
   inspection.
4. Run the verification:
   - Warden steps (01–04, 06):
     ```bash
     cd warden
     uv run pytest -q
     uv run ruff check .
     uv run ruff format --check .
     uv run mypy
     ```
   - CLI/asset parts (step 05, and 06's container test):
     ```bash
     uv run --with pytest python -m pytest tests/cli/ tests/container/ -q
     uv run mypy
     ```
5. Diff review.
6. **One commit per step**, exact message given in the step file, then flip
   this index's Status column in that same commit.

## Hard rules (apply to every step, no exceptions)

* **Identity:** author and committer are the repo identity **EddyXorb**
  (already configured locally; change nothing). **No** co-authorship
  trailers, **no** "Generated with" lines, **no** mention of AI assistants or
  tools anywhere — not in commit messages, not in branch names, not in MR/PR
  titles or descriptions.
* **Docstrings** (new and edited code):
  - never reference `docs/design/` paths;
  - no historical notes ("formerly", "was called X", design-doc section
    numbers, commit references);
  - never state the obvious;
  - as short as possible — only the interesting or surprising facts needed to
    grasp how the thing works;
  - no Sphinx cross-reference roles (`:class:`, `:func:`, `:mod:`, `:data:`)
    and no heavy markup — plain prose that reads well without an IDE.
* **Fail-closed everywhere:** unknown action ids, unknown field values,
  unmatched requests → deny/`ConfigError`; never widen to "probably fine".
* **No runtime rebuilds:** effective action sets are computed once at startup.
* **Intermediate commits are never red:** every step leaves the full
  verification green.
