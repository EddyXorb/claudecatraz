# 04 — Kernel action gates, capability-layer removal, assembly

Derives from main document §2.1, §2.3, §4. Depends on: 01–03.

## Goal

The two action gates move into the kernel where they run for every guard,
before `enrich`; the capability layer disappears; endpoint types derive their
valid actions from the guards instead of step 02's placeholder data.

## Change

* `core/guard.py`:
  - `Guard` gains an abstract `catalog` (its recognizer tuple) and a
    `supported` property (its `SUPPORTED` set); the kernel recognizes via
    `first_match` during `handle`, right after `parse`.
  - `kernel_gates` order: host allowlist → mode gate (writes) → project
    allowlist → **criticality gate** (any recognized action `>= IRREVERSIBLE`
    → deny, rule R4) → **action gate** (every recognized action id in
    `cfg.effective_actions(host)` → else deny, rule R6). Both run **before**
    `enrich` — a disabled or irreversible action must never trigger the
    credential-backed MR lookup.
  - Remove the `capability_gate` hook from the ABC and from `handle`; remove
    the interim gates the level-03 steps placed in the guards' hooks.
  - Unmatched request (`first_match` → `None`) or empty recognized set:
    writes deny here (R3); reads with a project pass to the guard's `decide`
    (project-bound read pass-through stays guard logic — the catch-all
    `project.read` row makes this reachable only for exotic methods).
* Delete `core/capabilities.py` and every remaining import of it. The two
  speculative capability names (privilege escalation, data destruction) are
  *not* ported — a future namespace expresses them as `IRREVERSIBLE` actions.
* `guards/git/endpoints.py`: replace step 02's explicit per-type action-id
  data with the derivation — union of `SUPPORTED` over the type's guards
  (resolve guard names to classes at assembly in `app.py`/`context.py`).
  Remove the `TODO(10-04)` marker. Config validation semantics unchanged
  (explicit-invalid → `ConfigError`, inherited-intersect).
* `full_decide` helpers in both guards: compose the new kernel order so tests
  exercise exactly the effective sequence.

## Constraints

* Core still imports no guard package; assembly code (`app.py`/`context.py`)
  is the only place names resolve to classes.
* Pipeline order is kernel-owned: a guard can no longer reorder or skip a
  gate.

## Tests (rework `test_capabilities.py` → `tests/core/test_action_gates.py`, plus `test_app.py`, `test_main.py`)

* Criticality gate: an irreversible action denies for every guard, rule R4,
  regardless of config.
* Action gate: disabled action denies with R6 and the action id in the
  reason; enabled action passes.
* Order proof: a write to a disabled action performs **no** upstream lookup
  (assert the MR-namespace client is never called).
* Type derivation: `plain` = transport's `SUPPORTED`; `gitlab` = union;
  explicit config outside the derived set still aborts startup.
* Grep-level assertion in CI fashion: `core/capabilities.py` gone,
  no `capability_gate` reference left.

## Verification

Warden commands from `00-index.md` step 4.

## Commit

```
warden: kernel action gates, remove capability layer
```

Identity/docstring rules: see `00-index.md`. Flip Status in `00-index.md` in
the same commit.
