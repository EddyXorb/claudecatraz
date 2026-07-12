# 03 — operator-surface-follow-through

Derives from [`../12-per-host-scoping.md`](../12-per-host-scoping.md) §4. On
contradiction the main doc wins — report it, do not guess. Runs last, after the
runtime it configures.

## How

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

## Tests

`test_init_wizard.py`, `test_doctor_*`, `test_warden_integration.py` — the
generated config loads and enforces per-endpoint project scope through the
warden's `config_load`.
