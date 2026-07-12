# 01 — project-scoping-per-host

Derives from [`../12-per-host-scoping.md`](../12-per-host-scoping.md) §2.1. On
contradiction the main doc wins — report it, do not guess. Security-sensitive:
land the cross-host isolation test first, then delete the global path.

## How

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

## Tests

A two-endpoint config where project P is on host A only — assert an A-request
for P allows and a B-request for P denies (path form and numeric-id alias);
update `test_config.py`, guard/kernel tests, gitlab guard tests.
