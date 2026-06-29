# Remove the `prune` command (P9)

> Status: draft · Topic: cli-worklist · Iterations planned: 1

## Goal
Delete the `catraz prune` subcommand and its backing `image.prune()`. It is a thin
wrapper over `docker image rm catraz-base:*` and does not earn permanent CLI surface.

## Context / constraints
- Parser + handler: `src/catraz/cli.py`
  - `cmd_prune` (lines 47–51)
  - `sub.add_parser("prune", parents=[_g()], …)` (line 114)
  - `"prune": cmd_prune` in `HANDLERS` (line 156)
- Implementation: `src/catraz/image.py` `prune()` (lines 37–41)
- `from catraz import image as _image_mod  # noqa: F401` (cli.py:24) — **leave it**.
  (Rationale note: nothing actually imports it via `cli`; `tests/cli/test_image.py`
  does `from catraz import image` directly. Removing the re-export is harmless but
  out of scope — don't touch it.)
- No test references `catraz prune` or `image.prune` (verified: `grep -rn prune tests`
  returns nothing). `tests/cli/test_image.py` covers `_build_base`/`resolve_base` only.
- Historical design docs under `docs/design/agentic-workflow/05-packaging/` mention
  `prune`. These are dated design records — **do not rewrite history**; leave them.
- README's command table does **not** list `prune` — no README change needed (verify).

## Approach
Pure surface reduction. Remove the three cli.py sites and the `image.prune` function.
Do **not** fold the behavior into `doctor --fix` (out of scope; `docker image rm
catraz-base` / `docker image prune` already cover the need).

## Steps
1. `src/catraz/cli.py`: delete `cmd_prune` (47–51, **including its trailing blank line**
   so no double blank gap remains before `cmd_version`), the `prune` subparser line (114),
   and the `"prune": cmd_prune` entry in `HANDLERS` (156). After this, the
   "commands that stay in cli.py" comment block holds only `cmd_version` — leave the
   comment (still accurate: "< 7 lines, no module worth making").
2. `src/catraz/image.py`: delete `prune()` (37–41).
3. Add a regression test `tests/cli/test_prune_removed.py`:
   - `build_parser()` rejects `prune`: `parser.parse_args(["prune"])` raises `SystemExit`.
   - `"prune"` not in `cli.HANDLERS`.
   - `not hasattr(catraz.image, "prune")`.
4. `grep -rn "prune" src` returns nothing. (Repo-wide, the only surviving hits are under
   `docs/design/...` — intentional history — plus unrelated `_prune_agent_logs`/`git prune`
   noise; none under `src/`.)

## Success criteria
- `catraz prune` exits non-zero with an argparse "invalid choice" error.
- `grep -rn prune src` is empty.
- `pytest tests/cli` is green (new test included).

## Risks & open questions
- None material. `_image_mod` re-export must stay (other tests import `catraz.image`).

## Revision history
- v0: initial draft
- v1 (roast iter 1): no criticals. Accepted 3 nits — corrected the `_image_mod`
  rationale (nothing imports it via `cli`), pinned "delete trailing blank line" in step 1,
  and made the grep wording precise (`src/` yields zero hits; doc hits are repo-wide).
