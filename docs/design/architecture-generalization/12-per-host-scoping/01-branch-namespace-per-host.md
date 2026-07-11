# 01 — branch-namespace-per-host

Derives from [`../12-per-host-scoping.md`](../12-per-host-scoping.md) §2.2. On
contradiction the main doc wins — report it, do not guess. Independent of
project-scoping in the guards, but both touch `Config`/`config_load` and
`kernel_gates` — do project-scoping first, then rebase this onto it.

## How

* **Config** (`warden/warden/core/config.py`): `in_branch_namespace(self, host,
  name)` tests `effective_rules(host).branch_prefixes`; delete the global
  `Config.branch_prefixes` field and the no-host `in_branch_namespace`. The
  global default now flows from `[git.rules].branch_prefixes` through the
  existing `effective_rules` cascade.
* **Call sites** (all pass the intent/MR host): `transport/policy.py:68`,
  `transport/reconcile.py:22`, `gitlab/policy.py:93`, `gitlab/reconcile.py:33`,
  `gitlab/mr_namespace.py:56`.
* **Loader** (`config_load.py`): drop `_tunable_branch_prefixes` and the
  top-level `branch_prefixes`/`branch_prefix` parse; `[git.rules].branch_prefixes`
  (already parsed by `_parse_rules`) is the global default.
* **Validation** (`config_load.py:_branch_prefixes_problems`): validate the
  effective global namespace (`git_rules.branch_prefixes` or built-in) is
  non-empty and has no empty element.

## Tests

An endpoint override narrows the namespace on its host without affecting another
host; the `[git.rules]` global applies where no override is set; an empty
effective global still aborts startup.
