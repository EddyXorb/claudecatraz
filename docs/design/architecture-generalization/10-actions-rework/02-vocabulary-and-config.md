# 02 — Git-namespace vocabulary + config cascade on the new ids

Derives from main document §3 (the table, verbatim), §4, §5. Depends on: 01.

## Goal

The vocabulary becomes real and the config layer speaks it. The two old
guards keep working through a small, explicitly temporary **bridge** (deleted
by the level-03 steps) so this commit stays green without rewriting the
guards yet.

## Create

* `warden/warden/guards/git/actions.py` — the twenty `Action` instances from
  the main document's table (§3), exactly those ids and criticalities, plus:
  - `ALL: frozenset[Action]` — the vocabulary;
  - `DEFAULT: frozenset[Action]` — the ✔ rows of the table (never-class and
    opt-in rows excluded);
  - `by_id: Mapping[str, Action]`.
  Module-level check: ids unique, every `DEFAULT` member in `ALL`, no
  `IRREVERSIBLE` member in `DEFAULT`.

* `warden/warden/guards/git/endpoints.py` — `ENDPOINT_TYPES` with the types
  `"plain"` and `"gitlab"`. **In this step only**, each `EndpointType` also
  carries its valid-action id set as explicit data
  (`plain` → the `repo.*` ids, `gitlab` → all twenty); step 04 replaces this
  data with the derivation from the guards' `SUPPORTED` unions. Mark the field
  with a `TODO(10-04)` comment. `"github"` stays a reserved, rejected type.

## Change

* `warden/warden/core/config.py` / `config_load.py` — all action
  validation/cascade now imports `warden.guards.git.actions` /
  `warden.guards.git.endpoints` instead of `warden.guards.gitlab_api.actions`:
  - `effective_actions(host)` cascade unchanged in shape (endpoint override →
    `[git].actions` → `DEFAULT`); missing key ≠ empty list; lists replace
    completely.
  - Explicit per-endpoint list with an id outside its type's set →
    `ConfigError`. Inherited namespace default: silently intersect with the
    type's set. Unknown id anywhere → `ConfigError`.
  - Return type may stay `tuple[str, ...]`/`frozenset[str]` of ids — core
    keeps treating ids as opaque strings.

* **Bridge (temporary, both guards):** map old internals onto the new ids so
  requests still flow:
  - `guards/git/actions.py` (old module) `action_for_git_operation`: return
    `"repo.read"` for advertise/upload-pack, `"repo.branch.push"` for
    receive-pack + push discovery (coarse — per-ref precision arrives in
    03-transport).
  - `guards/gitlab_api`: a dict from old recognizer ids to new action ids
    (`mr.create → project.mr.create`, `mr.note/discussion/discussion_reply →
    project.mr.comment`, `mr.update → project.mr.edit`, `pipeline.trigger →
    project.ci.trigger`, `branch.create → repo.branch.create`,
    `issue.create → project.issue.create`) used by
    `build_effective_table`; reads stay on the old read-table path for now.
  Mark every bridge symbol `_BRIDGE_10_03` so the level-03 steps can grep and
  delete them.

* `warden/warden/warden.toml` test fixtures and `warden/tests/test_config.py`
  / `test_toml_codec.py` / `test_rules.py`: new ids.

## Constraints

* The vocabulary table in the main document §3 is normative — do not add,
  rename, or reclassify actions here.
* No behavior widening through the bridge: anything the old gate denied must
  still be denied.

## Tests

* `warden/tests/test_git_vocabulary.py` (new): twenty ids, uniqueness,
  criticality classes per table, `DEFAULT` = exactly the ✔ rows, no
  irreversible action in `DEFAULT`.
* `test_config.py`: cascade with new ids; explicit-invalid → `ConfigError`;
  inherited-intersect for a `plain` endpoint; unknown id → `ConfigError`;
  missing key ≠ empty list.
* Existing proxy tests stay green through the bridge (adjust expected action
  ids in assertions where they leak into messages).

## Verification

Warden commands from `00-index.md` step 4.

## Commit

```
warden: git-namespace action vocabulary, config cascade on new ids
```

Identity/docstring rules: see `00-index.md`. Flip Status in `00-index.md` in
the same commit.
