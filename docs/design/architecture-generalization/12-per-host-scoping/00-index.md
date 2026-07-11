# 12 — Per-host project + branch scoping — implementation steps

These derive from the main document
[`../12-per-host-scoping.md`](../12-per-host-scoping.md) (the *what/why*). On
contradiction the main document wins — report it, do not guess.

## Order and progress

The number is the dependency level: same number = independent (parallelizable,
separate commit); a higher number requires all lower ones. Flip the Status
column in the same commit as the step.

| Level | Step | Purpose | Status |
| --- | --- | --- | --- |
| 01 | project-scoping-per-host | route the project gate through `git_project_allowed(host, project)`; host-aware `Guard.project_allowed`/`project_gate`/`kernel_gates`; per-host `ApiGuard` id-aliases; delete global `Config.project_allowed` + top-level `allowed_projects` | ☐ |
| 01 | branch-namespace-per-host | `in_branch_namespace(host, name)` off `effective_rules(host).branch_prefixes`; update all five call sites; global default in `[git.rules]`, endpoint override; delete top-level `branch_prefixes`/`branch_prefix` + `Config.branch_prefixes` | ☐ |
| 02 | drop-legacy-top-level-quotas | delete dead top-level `max_open_mrs`/`max_open_branches`/`max_writes_per_hour`/`max_push_bytes` from `Config` + parsing; refuse removed top-level keys at startup with a migration message | ☐ |
| 03 | operator-surface-follow-through | wizard writes endpoint-scoped `allowed_projects` + `[git.rules]` branch default; doctor validates per-endpoint; template + `config/README.md` (drop F1 caveat); catraz tests | ☐ |

Levels 01 are disjoint in the guards but both touch `Config`/`config_load` and
`kernel_gates`; do project-scoping first, then rebase branch-namespace onto it.
Level 02 is the tidy that 01 makes safe (enforcement no longer reads the
top-level fields). Level 03 is the catraz surface and must land last, after the
runtime it configures.

## Step 01 — project-scoping-per-host (the *how*)

Main doc §2.1. Security-sensitive: land the cross-host isolation test first.

* **Kernel** (`warden/warden/core/guard.py`): `project_gate(project, host,
  project_allowed)` and `kernel_gates(...)` call `project_allowed(intent.host,
  intent.project)`; the callable type becomes `Callable[[str, str], bool]`.
  `Guard.project_allowed(self, host, project)` defaults to
  `cfg.git_project_allowed(host, project)`; `handle` passes the intent's host.
* **GitLab guard** (`warden/warden/guards/git/gitlab/guard.py`): make
  `project_id_aliases` a `Mapping[str, set[str]]` keyed by normalised host;
  `project_allowed(self, host, project)` =
  `cfg.git_project_allowed(host, project) or normalize_project(project) in
  aliases[host]`. `reconcile_mrs` (and `reconcile.py`) return per-host resolved
  ids; wire them into the per-host map.
* **Policy wrappers** (`warden/warden/guards/git/transport/policy.py`,
  `.../gitlab/policy.py`): `full_decide`'s `project_allowed` default becomes
  `cfg.git_project_allowed`; the callable type gains the host arg.
* **Config** (`warden/warden/core/config.py`, `config_load.py`): delete
  `Config.project_allowed`, the `allowed_projects` field, and
  `_tunable_projects("allowed_projects")`. Keep `git_project_allowed` /
  `GitEndpoint.project_allowed`.
* **Startup** (`warden/warden/__main__.py`): replace the empty-global warning
  with a per-endpoint warning ("endpoint <host>: allowed_projects empty — all
  ops on this host denied").
* **Tests**: a two-endpoint config where project P is on host A only — assert an
  A-request for P allows and a B-request for P denies (path form and numeric-id
  alias); update `test_config.py`, guard/kernel tests, gitlab guard tests.

## Step 01 — branch-namespace-per-host (the *how*)

Main doc §2.2.

* **Config** (`config.py`): `in_branch_namespace(self, host, name)` tests
  `effective_rules(host).branch_prefixes`; delete the global
  `Config.branch_prefixes` field and the no-host `in_branch_namespace`. Global
  default now flows from `[git.rules].branch_prefixes` through the existing
  `effective_rules` cascade.
* **Call sites** (all pass the intent/MR host): `transport/policy.py:68`,
  `transport/reconcile.py:22`, `gitlab/policy.py:93`, `gitlab/reconcile.py:33`,
  `gitlab/mr_namespace.py:56`.
* **Loader** (`config_load.py`): drop `_tunable_branch_prefixes` and the
  top-level `branch_prefixes`/`branch_prefix` parse; `[git.rules].branch_prefixes`
  (already parsed by `_parse_rules`) is the global default.
* **Validation** (`config_load.py:_branch_prefixes_problems`): validate the
  effective global namespace (`git_rules.branch_prefixes` or built-in) is
  non-empty and has no empty element.
* **Tests**: an endpoint override narrows the namespace on its host without
  affecting another host; the `[git.rules]` global applies where no override is
  set; empty effective global still aborts.

## Step 02 — drop-legacy-top-level-quotas (the *how*)

Main doc §2.3.

* **Config** (`config.py`): delete the top-level `max_open_mrs` /
  `max_open_branches` / `max_writes_per_hour` / `max_push_bytes` fields (git
  enforcement already reads `effective_rules(host)`). Check `max_push_bytes`
  users — the push-size cap in `transport/policy.py:decide` reads
  `cfg.max_push_bytes`; move it to `effective_rules(host).max_push_bytes`.
* **Loader**: delete the matching `_tunable_int` calls; add a top-level
  unknown/removed-key check that raises `ConfigError` naming `[git.rules]` or the
  endpoint for any of the removed keys.
* **Tests**: a config with a stale top-level key fails to load with the guiding
  message; the quota ceilings still resolve from `[git.rules]`/endpoint.

## Step 03 — operator-surface-follow-through (the *how*)

Main doc §4. Runs last.

* **policy** (`src/catraz/policy.py`): a setter that writes `allowed_projects`
  inside a named `[[git.endpoint]]`; a setter for `[git.rules].branch_prefixes`.
* **Wizard** (`_wizard_interactive.py`, `_wizard_yes.py`): write the project
  allowlist onto the endpoint and the branch prefix into `[git.rules]`; stop
  writing the top-level keys.
* **Doctor** (`src/catraz/doctor.py`): validate the endpoint's `allowed_projects`
  and the `[git.rules]` branch default; drop the top-level project/branch checks.
* **Template + README** (`assets/config/warden.toml`, `assets/config/README.md`):
  `branch_prefixes` under `[git.rules]`, drop top-level `allowed_projects`/`max_*`,
  show `allowed_projects` in the endpoint example, remove the F1 caveat.
* **Tests**: `test_init_wizard.py`, `test_doctor_*`, `test_warden_integration.py`
  (generated config loads and enforces per-endpoint project scope through the
  warden's `config_load`).

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
