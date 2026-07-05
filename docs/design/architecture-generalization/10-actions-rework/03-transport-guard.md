# 03 — Transport guard: `guards/git/transport/` on recognizers

Derives from main document §2.2–§2.4, §4, §6. Depends on: 01, 02. Parallel
with `03-gitlab-guard.md` — in shared wiring files (`app.py`, `context.py`)
touch **only this guard's** import/registration lines.

## Goal

The git smart-HTTP guard becomes a recognizer-based guard under
`guards/git/transport/`, recognizing per-ref-command action sets. It is the
shared transport for **every** git-namespace endpoint type — never rename it
"plain".

## Move / create

* Move `warden/warden/guards/git/{guard,intent,policy,pktline,state,reconcile,errors}.py`
  → `warden/warden/guards/git/transport/`. (`guards/git/` root now holds only
  `actions.py`, `endpoints.py`, `__init__.py` — the namespace files from 02.)
* `guards/git/transport/actions.py`:
  `SUPPORTED = frozenset({repo.read, repo.branch.create, repo.branch.push,
  repo.branch.delete, repo.tag.create, repo.tag.delete})` (referencing
  `guards.git.actions` members, accessed qualified). Startup subset check
  against the namespace vocabulary.
* `guards/git/transport/recognizers.py` — `GitRecognizer(Recognizer[GitIntent])`
  rows:
  - advertise (either service) and upload-pack → `{repo.read}` (push
    discovery maps to what it does — reads refs; the write-token forwarding
    stays untouched transport plumbing);
  - receive-pack → per-ref-command classification, unioned over the batch:
    * zero-new-oid on `refs/heads/*` → `repo.branch.delete`
    * zero-new-oid on `refs/tags/*` → `repo.tag.delete`
    * non-delete on `refs/tags/*` → `repo.tag.create`
    * create on `refs/heads/*` → `repo.branch.create`
    * update on `refs/heads/*` → `repo.branch.push`

## Change

* Delete the old `guards/git/actions.py` (the `action_for_git_operation`
  ladder) including its `_BRIDGE_10_03` variant from step 02.
* `policy.py`: R2 (branch namespace), R5 (quotas, push size, fail-safe lock)
  stay as they are — scope/quota is orthogonal to actions. The action check
  becomes: recognize → criticality/membership gate. **Interim:** run that gate
  inside the existing `capability_gate` hook (the kernel takes it over in
  step 04); tag/delete handling needs no extra check anymore — the actions'
  `IRREVERSIBLE` class denies them.
* Per-ref deny responses (`deny_response`/`git_reject_response`) name the
  denied action per ref (e.g. `action repo.branch.push not enabled`) — with
  pushing disabled the denial happens here, not at discovery.
* Audit fields include the recognized action ids.

## Constraints

* Batch atomicity unchanged: one denied ref-command denies the whole push.
* Quota accounting (`record`, branch counter) keys stay unchanged — this step
  does not touch state layout.
* Fail-closed: an unrecognized operation/ref shape recognizes to `frozenset()`
  → deny (no `ValueError` escape hatches).

## Tests (rework `test_git_proxy.py`, `test_policy.py`, add `tests/transport/test_recognizers.py`)

* Ref-command classification: create/update/delete on heads, create/delete on
  tags → exact action sets; mixed batch unions correctly.
* Push discovery recognizes `{repo.read}`; with all `repo.branch.*` disabled,
  discovery passes and receive-pack denies per-ref with the action id in the
  message.
* Tag push and branch delete denied by criticality even when every
  configurable action is enabled.
* R2/R5 behavior unchanged (existing tests keep passing after id updates).

## Verification

Warden commands from `00-index.md` step 4.

## Commit

```
warden: rewrite git transport guard onto recognizers
```

Identity/docstring rules: see `00-index.md` — module moves are the moment to
strip the old docstrings' design-doc references and history notes in every
file touched. Flip Status in `00-index.md` in the same commit.
