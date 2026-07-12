# 12 — Per-host project + branch scoping — implementation steps

These derive from the main document
[`../12-per-host-scoping.md`](../12-per-host-scoping.md) (the *what/why*); each
step's *how* is its own file beside this index. On contradiction the main
document wins — report it, do not guess.

## Order and progress

The number is the dependency level: same number = independent (parallelizable,
separate commit); a higher number requires all lower ones. Flip the Status
column in the same commit as the step.

| Level | Step | Status |
| --- | --- | --- |
| 01 | [project-scoping-per-host](01-project-scoping-per-host.md) | ☑ |
| 01 | [branch-namespace-per-host](01-branch-namespace-per-host.md) | ☑ |
| 02 | [drop-legacy-top-level-quotas](02-drop-legacy-top-level-quotas.md) | ☐ |
| 03 | [operator-surface-follow-through](03-operator-surface-follow-through.md) | ☐ |

Both level-01 steps are disjoint in the guards but both touch
`Config`/`config_load` and `kernel_gates`; do project-scoping first, then rebase
branch-namespace onto it. Level 02 is the tidy the level-01 steps make safe.
Level 03 is the catraz operator surface and lands last.

## Verification (every step)

```
cd warden && uv run --extra dev pytest -q
uv run --with pytest python -m pytest tests/cli tests/container -q
uv run mypy
```

## Hard rules

Identity **EddyXorb**, no AI/tool mentions anywhere. One commit per step, green
each time. Security steps land the cross-host isolation test before deleting the
global path. Docstrings/comments per `docs/RULES.md`.
