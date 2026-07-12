# 02 — drop-legacy-top-level-quotas

Derives from [`../12-per-host-scoping.md`](../12-per-host-scoping.md) §2.3. On
contradiction the main doc wins — report it, do not guess. Requires both level-01
steps: enforcement no longer reads the top-level fields, so removing them is safe.

## How

* **Config** (`warden/warden/core/config.py`): delete the top-level
  `max_open_mrs` / `max_open_branches` / `max_writes_per_hour` / `max_push_bytes`
  fields — git enforcement already reads `effective_rules(host)`. The push-size
  cap in `transport/policy.py:decide` reads `cfg.max_push_bytes`; move it to
  `effective_rules(host).max_push_bytes`.
* **Loader** (`config_load.py`): delete the matching `_tunable_int` calls; the
  quota `> 0` validation moves onto every set `GitRules` knob (`_quota_problems`),
  matching the per-source branch-prefix validation.

The loader's stale-top-level-key rejection lands in step 03, in the same commit
as the template/wizard/doctor migration that stops emitting those keys — the
rejecter and the emitters must change together for each commit to stay green.

## Tests

The quota ceilings still resolve from `[git.rules]` / endpoint overrides; a
non-positive quota in either source aborts startup.
