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
* **Loader** (`config_load.py`): delete the matching `_tunable_int` calls; add a
  top-level removed-key check that raises `ConfigError` naming `[git.rules]` or
  the endpoint for any removed key (`branch_prefixes`/`branch_prefix`,
  `allowed_projects`, `max_*`) — a stale key fails fast, never silently ignored.

## Tests

A config with a stale top-level key fails to load with the guiding message; the
quota ceilings still resolve from `[git.rules]` / endpoint overrides.
