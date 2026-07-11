# 12 — Per-host project + branch scoping (F1/F2)

Document 11 §4 flagged two runtime gaps and routed the operator surface around
them. This document closes them. Every routable host is a `[[git.endpoint]]`
(08→10); the config *model* already resolves per-host actions
(`effective_actions`), quotas (`effective_rules`), project allowlists
(`git_project_allowed`), and access mode (`access_mode`). What is missing is that
the **enforcement callers** still reach past the per-host model to global,
host-agnostic checks — so per-host isolation is parsed and validated but not
enforced. This is the *what/why*; the step-by-step *how* lives in the subfolder
[`12-per-host-scoping/`](12-per-host-scoping/00-index.md) and derives from here —
on contradiction, this document wins.

> Prerequisite: 08, 09, 10 and 11 implemented. Pre-1.0 cleanup — a stale
> top-level key is refused at startup (fail-closed), not silently honoured; no
> back-compat shim.

---

## 1. What is broken today

* **F1 — project scoping is global, not per-host.** The kernel authorises a
  project with `Guard.project_allowed(project)` → `Config.project_allowed`, a
  path-only match against the single top-level `allowed_projects`. The per-host
  `Config.git_project_allowed(host, project)` / `GitEndpoint.project_allowed`
  exist and are tested, but nothing calls them. Two hosts share one project
  allowlist; a project allowed on one host is allowed on every host.
* **F1b — numeric project-id aliases are global.** `ApiGuard.project_id_aliases`
  is one flat `set[str]` rebuilt from every endpoint's reconcile. Host A's
  numeric id `1234` therefore authorises host B's project `1234` — a cross-host
  leak unique to multi-host setups.
* **F2 — the branch namespace is global, the quota knobs are split.** Branch
  enforcement calls `Config.in_branch_namespace(name)` against the top-level
  `Config.branch_prefixes` at all five sites, while quota ceilings in the same
  code paths already resolve per host via `effective_rules(host)`. Meanwhile the
  top-level `max_open_*` / `max_writes_per_hour` / `max_push_bytes` fields are
  dead for git enforcement (the guards read `effective_rules`, which never
  consults them) yet still parsed, so `warden.toml` carries two idioms for the
  same knob.

## 2. The target

One idiom for git policy: a **global default** in `[git.rules]`, a **per-endpoint
override** in `[[git.endpoint]].rules`, and a **per-endpoint-only** project
allowlist. The cascade (`effective_rules`) and the per-host project check
(`git_project_allowed`) already implement this; the work is to route every
enforcement site through them and delete the global keys that no longer have a
job.

### 2.1 Project allowlist — per-endpoint only

`allowed_projects` lives only on the endpoint (`[[git.endpoint]].allowed_projects`).
There is no global project allowlist. Enforcement uses
`Config.git_project_allowed(host, project)`; an unconfigured host has no endpoint
and denies. An endpoint with an empty `allowed_projects` denies every project on
that host (fail-closed) until one is added — same posture as today's empty global,
now scoped.

The gate becomes host-aware end to end: `project_gate` and `kernel_gates` take
the intent's host, `Guard.project_allowed(host, project)` defaults to
`cfg.git_project_allowed(host, project)`, and the two `full_decide` wrappers
default to it as well. Numeric-id aliases move onto the guard per host —
`ApiGuard` keys them by normalised host, and `project_allowed(host, project)`
consults only that host's alias set, so host A's ids never satisfy host B.

### 2.2 Branch namespace — global default, per-endpoint override

`branch_prefixes` is a git rule knob like the quotas: its global default lives in
`[git.rules].branch_prefixes` (built-in fallback `["claude/"]`), and an endpoint
narrows it with `[[git.endpoint]].rules.branch_prefixes`. Enforcement resolves it
per host through the existing cascade: `in_branch_namespace` takes a host and
tests against `effective_rules(host).branch_prefixes`. All five call sites
(transport policy + reconcile, gitlab policy + reconcile, MR-namespace) pass the
intent's host.

### 2.3 One home per knob — the top-level legacy keys go

The top-level `branch_prefixes` / `branch_prefix`, `allowed_projects`, and
`max_open_mrs` / `max_open_branches` / `max_writes_per_hour` / `max_push_bytes`
keys are removed from `Config`, from parsing, and from the shipped template. Git
policy reads `[git.rules]` (global default) and `[[git.endpoint]]` (override +
project allowlist) only. `Config.project_allowed` and the global
`Config.in_branch_namespace` / `Config.branch_prefixes` field are deleted; their
per-host successors (`git_project_allowed`, `in_branch_namespace(host, …)`,
`effective_rules(host)`) are the single enforcement path.

A stale top-level key is a startup `ConfigError` naming its new home, not a
silent ignore: silently dropping `allowed_projects` would leave a host denying
everything with no explanation, and silently dropping a quota would loosen it —
both worse than refusing to start. Fail-closed validation keeps the effective
global branch namespace non-empty (an empty prefix list accepts every branch).

## 3. Behavioral changes vs. today

* Per-host project isolation is real: a project (by path or numeric id) is
  authorised only on the endpoint whose `allowed_projects` lists it.
* The branch namespace is per host: an endpoint may narrow or widen prefixes
  without touching other hosts; unset inherits the `[git.rules]` global.
* `warden.toml` has one idiom per knob. A config still carrying a top-level
  `allowed_projects` / `branch_prefixes` / `max_*` fails fast with a message
  pointing at `[git.rules]` or the endpoint.
* Single-host setups are unchanged in effect: one endpoint with its
  `allowed_projects` and the `[git.rules]` (or built-in) branch default behaves
  as the single-GitLab case did.

## 4. Operator surface follow-through

The 11 operator surface wrote the **top-level** `allowed_projects` and
`branch_prefixes` because that was what the runtime enforced (11 §4/F1). With
enforcement now per-host, the wizard, doctor, and template move with it:

* **Wizard** writes `allowed_projects` inside the `[[git.endpoint]]` it
  synthesises, and the branch prefix into `[git.rules]` (global) — never
  top-level. `policy.py` gains an endpoint-scoped project-list setter beside
  `ensure_git_endpoint`.
* **Doctor** validates the endpoint's `allowed_projects` and the `[git.rules]`
  branch default; the top-level project/branch checks are removed. The 11 §4/F1
  caveat in `config/README.md` is dropped now that per-endpoint `allowed_projects`
  is enforced.
* **Template** `warden.toml` moves `branch_prefixes` under `[git.rules]`, drops
  the top-level `allowed_projects`/`max_*`, and shows `allowed_projects` in the
  commented `[[git.endpoint]]` example.

## 5. Conventions for the implementation

Author and committer are the repo identity **EddyXorb**; no co-authorship or
"generated with" trailers, no mention of AI tools anywhere. One commit per step,
each leaving the full verification green: `cd warden && uv run --extra dev pytest
-q` for the warden change, `uv run --with pytest python -m pytest tests/cli
tests/container -q` and `uv run mypy` for the catraz surface. Docstrings and
comments follow `docs/RULES.md`: short, no history, no obvious statements, no
markup, and no references to files under `docs/design/`. Security-sensitive: each
enforcement step lands with the test that proves host A cannot reach host B's
project/branch before the global path is deleted.
