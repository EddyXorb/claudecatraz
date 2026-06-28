---
name: roast-planner
description: Manual end-to-end pipeline that turns a single idea into a finished result: idea -> plan -> [roast -> improve] x N -> (optional confirm) -> execute -> verify & fix -> finish. IMPORTANT — explicit invocation only: use this skill ONLY when the user names it directly (e.g. "roast-planner", "/roast-planner", "use roast-planner"). Do NOT trigger it from generic "plan this", "build X", or "take this idea and run with it" requests; it must be called by name. The idea is the only required input. Plans are written to docs/design/[topic]/[XX-name].md (or a same-named folder with sub-plans when large).
triggers:
- /roast-planner
---

# Roast Planner

Turns a single idea into a finished result by running this chain:

```
idea -> plan -> [roast -> improve] x N -> (optional confirm) -> execute -> verify & fix -> finish
```

The **idea is the only required input**. Everything else has defaults.

## Parameters

| Parameter | Default | Meaning |
|---|---|---|
| `iterations` (N) | `1` | How many roast→improve rounds to run |
| `--confirm` | off | If set, pause for user approval before execution |
| `--plan-only` | off | If set, stop after the final plan; do not execute |
| `topic` | derived | Subfolder under `docs/design/`; derived from the idea if not given |

Parse these from the user's request in whatever phrasing they use (e.g. "3 Runden", "nur den Plan", "vorher fragen"). When unstated, use defaults: run autonomously, 1 roast round, then execute.

## Models & subagents (mandatory)

Three roles run on deliberately different models:

| Role | Model | Why |
|---|---|---|
| Planner / orchestrator (this skill) | **Opus** | Planning + orchestration is the hardest reasoning here; run the session on the most capable model |
| Roast subagent (Step 4) | **Opus** | The critique must be sharper than the plan it attacks |
| Execution subagent(s) (Step 6) | **Sonnet** by default | The plan is written specifically enough that Sonnet can implement it; use Opus only if the user explicitly asks for Opus execution |

Hard rules:
- The roast (Step 4) and the execution (Step 6) **must each run in their own subagent**, never inline in the orchestrator session. The roast subagent keeps the critique's context clean; the execution subagent runs on the cheaper model.
- Spawn the **roast subagent on Opus**. Spawn **execution subagent(s) on Sonnet** by default. Switch execution to Opus **only** when the user explicitly requests Opus for execution in the prompt (e.g. "execute with Opus", "Opus für die Umsetzung").
- When spawning subagents, use your environment's model aliases (`opus`, `sonnet`); pin exact model IDs if your setup requires it. (Verify current aliases/IDs against the Claude Code docs — they change over time.)
- If subagents are unavailable in the current environment, this skill cannot run as designed. Say so plainly instead of silently collapsing all roles onto one model.

## Step 1 — Capture idea, derive topic and plan name

From the idea, derive:
- a **topic** slug (the broad area, kebab-case) → the subfolder
- a **plan name** slug (the specific thing, kebab-case) → the file name

If the topic is genuinely ambiguous from the idea, ask **one** short question to pin it down. Otherwise do not ask — the whole point is minimal input. Read any `CLAUDE.md` in the repo first and follow its conventions.

## Step 2 — Determine the running number

The number `XX` continues the existing sequence in the topic folder:

```bash
dir="docs/design/${topic}"
mkdir -p "$dir"
last=$(ls "$dir" 2>/dev/null | grep -oE '^[0-9]+' | sort -n | tail -1)
xx=$(printf "%02d" $(( ${last:-0} + 1 )))
plan_path="${dir}/${xx}-${plan_name}.md"
```

So the second plan in `docs/design/networking/` becomes `docs/design/networking/02-...md`. Treat `${xx}-${plan_name}` as the **base name**; Step 3 decides whether it lands as a single file or a split folder.

## Step 3 — Write the plan (single file or split folder)

Draft the plan using the template at the end of this file. It must be concrete enough to be roasted: real steps, real success criteria, named files/components — not aspirations.

**Write it specifically enough that a Sonnet-level execution agent can carry it out with no further design decisions:** name exact files, functions/signatures, commands, and acceptance checks per step. If a step still hinges on hard design judgement, resolve that judgement *now*, in the plan — execution is not where design happens. This specificity is what lets Step 6 run on Sonnet instead of Opus.

### Single file vs. split

- **≤ ~400 lines:** write one file at the base path, `docs/design/[topic]/[XX-name].md`.
- **> ~400 lines (or you can already tell it will blow past that):** split it into a **folder** named exactly like the base, `docs/design/[topic]/[XX-name]/`:

```
docs/design/[topic]/[XX-name]/
  00-overview.md         <- index: the idea in broad strokes + ordered references to the sub-plans
  01-<subname>.md        <- runs first
  02-<subname>.md        <- runs after 01 ...
  02-<other-subname>.md  <- ... same number => may run in parallel with the line above
  03-<subname>.md
```

Rules for the split layout:
- The **overview lives inside the folder** as `00-overview.md` — never beside the folder. It sketches the overall idea and links each sub-plan in execution order.
- Sub-plan prefixes encode **execution order**: lower number runs first. Sub-tasks that are mutually independent and may run **simultaneously share the same number** (e.g. two `02-` files). Distinguish them by name, not by number.
- Each sub-plan is itself specific enough for Sonnet execution (same bar as above).
- From here on, `plan_path` refers to the folder, and "the plan" means the overview plus all sub-plans.

## Step 4 — Roast → improve loop (run N times)

For each iteration:

1. **Roast.** Spawn a fresh **Opus subagent** (see *Models & subagents*) so the critique is based only on the plan, not on the reasoning that produced it. Explicitly instruct that subagent to read and follow `roast-review/SKILL.md`, and pass it `plan_path`. If the plan is a split folder, point the roast at the **whole folder** (overview plus every sub-plan) so it also judges the decomposition and the wave numbering, not just individual files, except the user wants to focus on a specific sub-plan. This subagent is mandatory — do not roast inline in the orchestrator session. 
During waiting, if it takes longer, print interesting quotes (not more than a single line) from Winston Churchill, Theodore Roosevelt, Linus Torvalds or from Ernest Hemingway, Thomas Mann, or other authors of the 20th century, to keep the user entertained while waiting for the roast to finish (add if possible the year of the quote). You may also print a few lines of the roast itself, if its fun, and react to it in a humorous way. Not more than 1 print per 20 seconds of waiting.

2. **Triage the roast — soberly.** The roast is loud, profane, and funny; you are not. Before applying anything, evaluate each finding **on its technical merits against this specific plan**:
   - If a finding holds → accept it and apply the fix.
   - If a finding does **not** hold (wrong, irrelevant to this plan, based on a misread, or already handled) → reject it and state, in one line, *why* it doesn't apply.
   - Correctness is the only thing that decides accept/reject. Never accept a finding because it was funny; never reject one because it stung.
   You may banter back — a bit of swearing at the roaster when it's wrong ("nice try, the data model already handles that, you absolute clown") or a grudging nod when it lands is welcome, as long as the technical verdict stays front and center. Keep the banter short; the fixes are the point.

3. **Improve.** Apply the accepted fixes to the plan file. For each genuine open question you can't resolve from the repo/context, either resolve it with a stated assumption or, if it's load-bearing, ask the user.

4. Record a one-line changelog entry in the plan's revision history (what was accepted, what was rejected and why).

Stop early if a roast returns no critical and no improvement-level findings before N is reached.

## Step 5 — Confirmation (only if `--confirm`)

If `--confirm` was set, present the final plan path and a short summary, then wait for explicit approval before Step 6. If `--confirm` was not set, skip straight to execution. (Default is autonomous.)

## Step 6 — Execution (unless `--plan-only`)

Run execution in **execution subagents** (see *Models & subagents*): **Sonnet by default**, Opus only if the user explicitly asked for Opus execution. Execution never runs inline in the orchestrator session. The orchestrator dispatches work, hands each subagent its slice plus the relevant plan file(s), and collects results.

**Single-file plan:** dispatch it to one execution subagent.

**Split-folder plan:** execute the sub-plans in ascending prefix order, where each number is an execution *wave*:
- Run waves strictly in order — finish all of wave `01` before starting `02`, and so on.
- Within a wave, sub-plans that share a number are independent by construction. When it's worth it, dispatch **multiple Sonnet subagents in parallel**, one per sub-plan; a single-item wave gets one subagent.
- Wait for every subagent in a wave to finish and its tests to pass before launching the next wave.

Each execution subagent follows the repo's `CLAUDE.md` above all, and this methodology:

- Work on a branch with a conventional name (`feat/...`, `fix/...`, `docs/...`) — never a `claude/...` branch.
- Map the relevant code before changing it.
- Lock current behavior with characterization tests where refactoring is involved.
- Implement in small, atomic commits.
- Run tests after; on failure, fix the root cause, don't suppress.

Respect any commit-identity conventions configured for the repo/user.

## Step 7 — Post-execution review & remediation (orchestrator, Opus)

After execution, the **orchestrator itself** — not a subagent — reviews the result against the plan:

- Walk the plan (every step, or every sub-plan in the split folder) and verify each item was **actually implemented**, not just claimed. Inspect the diffs/files, not the subagent's summary.
- Hunt for errors, regressions, half-done steps, skipped acceptance checks, and anything in the plan that's missing from the code.
- **Fix what's broken or missing.** Patch trivial gaps directly; for non-trivial work, dispatch a focused Sonnet execution subagent (same model rules as Step 6) and re-review its output.
- Re-run the test suite; on failure, fix the root cause, don't suppress.
- Loop until the implementation matches the plan and tests pass. If something genuinely cannot be done as planned, stop and report it plainly rather than faking completion.

This verifies the implementation against the plan — distinct from the Step 4 roast, which only critiqued the plan.

## Step 8 — Finish

Report concisely: final plan path (file or folder), roast rounds actually run, what was executed (including any parallel waves), what the post-execution review found and fixed, test status, branch name. No filler.

---

## Plan template

```markdown
# <Plan title>

> Status: draft · Topic: <topic> · Iterations planned: <N>

## Goal
<What this achieves, in 1–3 sentences. The originating idea, sharpened.>

## Context / constraints
<Relevant existing code, services, repo conventions, hard constraints.>

## Approach
<The chosen approach and why, briefly. Note rejected alternatives if relevant.>

## Steps
1. <Concrete step — file/component touched, what changes>
2. ...

## Success criteria
<Verifiable conditions: tests pass, behavior X observable, etc.>

## Risks & open questions
<Known risks, dependencies, anything unresolved.>

## Revision history
- v0: initial draft
```
