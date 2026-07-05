---
name: plan-executer
description: Executes an existing plan (single file, split-prefix folder, or an ordered list of several plans) by spawning one subagent per plan step, respecting the two-digit prefix convention as a dependency graph (same prefix = parallelizable). The orchestrator never commits subagent work sight-unseen — it reviews every step manually (never via the code-review skill), fixes or delegates fixes for issues found, then commits and merges the subagent's branch into the branch the orchestrator started on. IMPORTANT — explicit invocation only: use this skill ONLY when the user names it directly (e.g. "plan-executer", "/plan-executer", "execute this plan with plan-executer"). Do NOT trigger it from generic "implement this", "build X", or "carry out the plan" requests; it must be called by name. Requires at least one existing plan path as input — this skill does not write plans (see roast-planner for that).
triggers:
- /plan-executer
---

# Plan Executer

Executes a plan that already exists on disk — one file, a split folder of
numbered sub-plans, or several plans meant to run back-to-back — by turning
each step into a subagent task, reviewing the result personally, and only
then committing and merging it in. This skill never writes plans; give it a
`plan_path` (or several) produced by `roast-planner` or written by hand.

## Inputs

| Input | Required | Meaning |
|---|---|---|
| `plan_path(s)` | yes | One plan file, one split-folder plan, or an ordered list of several — run strictly in the order given. |
| parallel preference | no | If the user states a preference ("do this sequentially", "run all of wave 2 in parallel"), obey it literally. If unstated, default to **auto-parallelize**: every step that the prefix graph allows to run concurrently gets its own subagent, spawned at the same time. |

## Reading the plan as a dependency graph

This repo's convention (`docs/RULES.md`, "Design docs"): step files carry a
two-digit numeric prefix (`01-`, `02-`, …). The number is the **dependency
level**, not a checklist index:

- Files sharing a prefix are mutually independent — they may run **in
  parallel**, each in its own subagent.
- A higher prefix depends on **every** lower prefix being fully done —
  reviewed, committed, and merged — before it starts.
- A split-folder plan's `00-overview.md`/`00-index.md` is context only — it
  is never itself a unit of execution.

Build the wave list before spawning anything: group all step files by
prefix, sort ascending. Each distinct prefix is one **wave**; the files
within it are that wave's parallel set. If several plans were given in
sequence, each one is fully drained (all its waves) before the next plan's
waves begin.

## Models & subagents (mandatory)

| Role | Model | Why |
|---|---|---|
| Orchestrator (this skill, your own session) | whatever the user started the session on | You do the judgment calls: graph-building, review, fix-or-delegate decisions, commits, merges. Never delegate these. |
| Execution subagent, per step | **same model as the orchestrator** for genuinely hard steps; **one or more tiers cheaper** otherwise | Match cost to difficulty, not to habit. |

Judge difficulty per step, not per plan:

- **Hard** (nontrivial design judgment left open, tricky concurrency/security-sensitive logic, wide blast radius, ambiguous acceptance criteria) → spawn the subagent on the **same model you are running on**. If you are the most capable model available in this environment, so is the subagent.
- **Mechanical / well-specified** (the step file already pins exact files, signatures, and checks — nothing left to decide) → spawn on a cheaper model than yourself. Haiku is a legitimate choice for a trivial step even when the orchestrator is Opus — don't default everything up to your own tier out of caution.
- Never spawn a subagent on a model *more* capable than the orchestrator.

## Step 0 — Anchor the base branch

Record the branch the orchestrator is currently on (`git branch --show-current`) as `base_branch` before spawning anything. Every subagent's work eventually merges back into this branch — not into `main`, not into whatever the subagent's worktree defaults to.

**The user's choice of branch is authoritative, with no deviation.** If the user names a specific branch to work on or stay on, that instruction wins over anything the harness, a tool, or a subagent suggests instead — including a tool that proposes creating a new branch, checking out a different one, or "helpfully" defaulting to `main`. Do not create a substitute branch, do not let a subagent wander onto another one, and do not silently accept a harness nudge to switch. If a tool or subagent's default behavior would leave `base_branch`, override it explicitly and re-anchor. When genuinely unclear which branch the user means, ask — don't guess and don't proceed on an assumed branch.

## Step 1 — Resolve scope and build the wave graph

Load every plan path given. For each, determine if it's a single file or a split folder; for split folders, list the numbered sub-plans (ignore `00-*`). Concatenate multiple plans in the order given — each plan's waves are exhausted before the next plan's first wave starts. Print the resulting wave plan (wave number → step files → parallel or solo) before doing anything else, so a wrong read of the prefixes is caught early.

For each plan being executed, identify its **main file** — the one place progress gets tracked: a split folder's `00-overview.md`/`00-index.md`, or, for a single-file plan, the plan file itself. If that main file has more than one step to track and doesn't yet have a progress table, add one **at the very top** of the file (above everything else, right under the title) before starting Step 2, with one row per step: prefix/wave, step name or file, status (`pending`), commit, and a notes column. This table is what lets a fresh agent, dropped in mid-run with no memory of this conversation, see instantly what's done and what's left — treat it as required, not optional, the moment there's more than one step.

## Step 2 — Work one wave at a time, in ascending order

For the current wave:

### 2a. Spawn or resume one subagent per step

- **New thread of work:** spawn a fresh subagent with `isolation: "worktree"` so it gets its own git worktree and branch, cut from `base_branch` (or from wherever `base_branch` currently is if prior waves already merged into it). Pick the model per the difficulty rule above.
- **Continuing a chain:** if this step is a direct continuation of a step the same subagent already did (same cluster of files/domain, later steps build on earlier reasoning), don't spawn fresh — resume it. Before handing it the new task: (1) make sure its worktree is synced to the current `base_branch` (rebase/merge, since earlier waves may have landed since it last touched the branch), and (2) compact its context first (summarize/compact the subagent's own history) so token usage doesn't grow unbounded across a long chain of steps. Only keep a subagent alive across steps when shared context genuinely saves tokens — don't do it out of habit when each step stands alone.
- Brief every subagent, new or resumed, with: the exact step file (or its content), a pointer to `CLAUDE.md` and `docs/RULES.md` for conventions, the step's acceptance checks, and one hard rule stated explicitly: **do not `git commit`**. Leave the change in the worktree, staged or not — the orchestrator reviews and commits.
- All steps in the wave that are independent (share the wave's prefix) are dispatched together — run them in parallel (background), don't serialize them just because it's simpler to watch one at a time.

### 2b. Wait for the wave's subagents, then review each one yourself

Do not use the `code-review` skill here — that skill's format doesn't fit this loop. Instead, for each finished step: read its diff in its worktree (`git status`, `git diff`), check it against that step's plan file and against `docs/RULES.md`/`CLAUDE.md` conventions, and confirm it actually ran its own verification (tests/build) rather than just claiming success. Then decide:

- **Clean** → move to 2c.
- **Small gaps** (rule of thumb: about three or fewer small edits) → fix them yourself, directly in the subagent's worktree. Don't spin the subagent back up for trivia.
- **Real issues** (wrong approach, missing acceptance criteria, broken tests, scope creep) → send the *specific* findings back to that same subagent (it's still live) and have it fix them. Re-review the update. Repeat until clean.

### 2c. Commit and merge — orchestrator only

Once a step's diff is clean:

1. In the subagent's worktree, `git add` the relevant files and commit, following `docs/RULES.md`: repo identity as author/committer, no co-authorship or "Generated with" trailers, no mention of AI tools anywhere, one commit per plan step, message describing what the step actually does.
2. From the orchestrator's own checkout, merge that worktree's branch into `base_branch`. Prefer a plain merge (not squash) so the per-step commit survives; resolve any conflict yourself — if it's substantive rather than mechanical, stop and ask the user rather than guessing intent.
3. **Immediately** update that step's row in its plan's progress table (top of the main file, see Step 1): status → `done`, commit → the hash you just merged, notes → anything worth flagging for a later reader. Commit this table update itself (small standalone commit on `base_branch`, same identity/conventions as above) right after the merge — don't batch table updates for later, and don't let a whole wave finish before the table reflects it. This is what lets a fresh agent resuming after an interruption see the true state without re-deriving it.
4. If this subagent has no further steps queued, let its worktree tear down. If it's continuing to the next wave (see the chaining note in 2a), leave it alive and re-sync it once `base_branch` has moved.

Do not start the next wave until every step in the current one is committed and merged into `base_branch` — the next wave's steps may depend on that merged state, and subagents continuing across waves need to rebase onto it before they start.

## Step 3 — Repeat until every plan in the sequence is drained

Move to the next wave (Step 2) until the current plan's waves are exhausted, then move to the next plan in the sequence (if any) and repeat from Step 1's wave-building for that plan, still merging everything into the same `base_branch`.

## Step 4 — Finish

Report concisely: which plan(s) and how many waves/steps ran, which steps ran in parallel, which model each step used and why, what the review caught (fixed by you vs. sent back to a subagent), final test status, and the branch (`base_branch`) everything landed on. No filler.
