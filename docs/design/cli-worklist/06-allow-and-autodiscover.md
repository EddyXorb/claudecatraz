# `catraz allow` + GitLab-remote auto-offer in the wizard (P1)

> Status: draft · Topic: cli-worklist · Iterations planned: 1

## Goal
Two small pieces so allowed projects don't have to be hand-typed and edited:
(a) `catraz allow <path>…` — validate and append project path(s) to
`warden.toml`'s `allowed_projects`; (b) in the interactive wizard, scan the init folder's
git remotes for ones whose host matches `GITLAB_URL`, derive the project path, and **offer**
them as the prompt's default. Per R5/§6.10: keep the allowlist explicit and enumerable —
**offer, never auto-add silently**.

## Context / constraints
- Validation + TOML writing already exist in `src/catraz/policy.py`:
  - `validate_project(p)` → error reason or `None` (rejects wildcards/globs, leading/
    trailing slash, leaf names without `/`).
  - `_read_toml_allowed_projects(path)` → `list[str]` (tomllib, regex fallback).
  - `set_toml_list(path, key, values)` → comment-preserving write. **The shipped line is
    `allowed_projects = []`** (verified: `assets/config/warden.toml:20`) — an empty *list*
    parsing to `[]`. (Note: `set_toml_list`'s own docstring wrongly says `[""]`; it's stale.
    Because `[]` is falsy, the wizard's `if cur_proj and not args.force` guard does *not*
    early-return on a fresh init, so the discovery offer fires as intended.)
  - `_resolve_allowed_projects(root, env)` → `(list, source)` where source is
    `".env override"` | `"warden.toml"` | … (env `WARDEN_ALLOWED_PROJECTS` overrides toml).
- Wizard: `src/catraz/commands/setup/_wizard_interactive.py`
  - `_prompt_allowed_projects(root, env, warden_toml, args, out)` (81–112) early-returns
    when projects are already set and not `--force`; otherwise asks a free-text comma list,
    validates each, and `set_toml_list`s the valid ones.
  - Called at line 170 inside `_wizard_interactive`, right after the `GITLAB_URL` prompt
    (the `url` local is in scope at the call site).
  - The non-interactive `-y` path (`_wizard_yes`) must **not** auto-discover (that would be
    a silent auto-add) — leave it untouched.
- UI primitives: `Out.ask(prompt, default)` (free text, default on empty/EOF),
  `Out.info/warn`. There is **no** yes/no `confirm` helper — use `ask` with a prefilled
  default the user edits or clears.
- Command wiring: `src/catraz/cli.py` `build_parser` + `HANDLERS`; handler can live in
  `src/catraz/commands/setup/__init__.py` next to `cmd_sync` (cli already does
  `from catraz.commands import setup`).
- `find_root(args.dir)` already runs before non-`init` handlers (`cli.main`), so `cmd_allow`
  receives a resolved `root` with an existing `.catraz`.

## Approach
Reuse `validate_project` + `_read_toml_allowed_projects` + `set_toml_list` for the `allow`
command (merge, dedupe; defensively drop any `""` even though the real default is `[]`).
For discovery, add a **pure**
`_project_from_remote_url(url, gitlab_url)` parser (unit-tested hard) plus a thin
`_discover_gitlab_projects(root, gitlab_url)` that shells `git remote` over the init folder
and its immediate git subdirs. The wizard offers discovered projects as the `ask` default.

## Steps
1. **policy.py** — add two pure helpers (so they're testable without git/Docker):
   - `def _project_from_remote_url(url, gitlab_url) -> str | None`: parse host+path from
     both `https://host/group/sub/proj(.git)` and the scp-like `git@host:group/sub/proj(.git)`
     forms. **Do not** feed the SSH form to `urllib.parse` — it has no `//` and gets
     mangled; special-case `…@host:path` by splitting on the first `@` then `:`. For the
     HTTPS form, `urllib.parse.urlsplit` is fine. Return the path (`group/sub/proj`,
     leading slash + trailing `.git` stripped) **iff** the host equals `gitlab_url`'s host
     (default `gitlab.com`, compare host only, ignore port); else `None`. Run the result
     through `validate_project` and return `None` if it has a reason.
   - `def merge_allowed(existing, additions) -> list[str]`: drop falsy/empty strings from
     `existing` (defensive — real default is `[]`), append `additions`, dedupe preserving
     first-seen order. (Used by `cmd_allow`.)
2. **discovery scanner** — in `_wizard_interactive.py` (or policy.py) add
   `def _discover_gitlab_projects(root, gitlab_url) -> list[str]`:
   - Candidate dirs: `root` itself, plus its immediate subdirectories that contain a `.git`.
     `root` is **always** scanned; the bound below only caps the subdir sweep.
   - For each, `subprocess.run(["git","-C",dir,"remote","-v"], capture_output=True, text=True)`;
     on failure/`FileNotFoundError`(no git) skip.
   - Extract each remote URL (first whitespace-split column 2), map via
     `_project_from_remote_url`, collect non-None, dedupe preserving order. Cap the subdir
     sweep (e.g. first ~50 immediate subdirs) so a huge folder doesn't stall init; `root`
     is unaffected by the cap.
3. **wizard offer** — `_prompt_allowed_projects(...)` gains a `gitlab_url` param (pass `url`
   from the call site, line 170). Add the needed imports to `_wizard_interactive.py`
   (`_discover_gitlab_projects` if it lands in policy.py; nothing new if it's local).
   Before asking:
   - `discovered = _discover_gitlab_projects(root, gitlab_url)`.
   - If `discovered`: `out.info("  Detected GitLab project(s) from git remotes: " + ", ".join(discovered))`
     and use `", ".join(discovered)` as the **default** of the `ask` (user edits/clears to
     decline — this is the "offer", an explicit Enter accepts). If none, keep the current
     empty default.
   - Everything downstream (validate each, `set_toml_list`) is unchanged.
4. **`cmd_allow`** in `setup/__init__.py` (add the imports — this module imports **no**
   policy symbols today: `from catraz.policy import validate_project,
   _read_toml_allowed_projects, merge_allowed, set_toml_list, _resolve_allowed_projects`;
   `load_env` is already imported):
   - `warden_toml = root/".catraz/config/warden.toml"`; if missing → `CliError("not set up — run catraz init", EXIT_CONFIG)`.
   - Validate each of `args.projects` via `validate_project`; collect valid, `out.warn` each invalid.
   - If no valid → `out.err("nothing to add")`, `return EXIT_CONFIG`.
   - `existing = _read_toml_allowed_projects(warden_toml)`; `merged = merge_allowed(existing, valid)`.
   - If `merged == [x for x in existing if x]` (no net change) → `out.info("already allowed — nothing to add")`, `return EXIT_OK`.
   - `set_toml_list(warden_toml, "allowed_projects", merged)`; `out.info(out.green(f"• allowed_projects now: {', '.join(merged)}"))`.
   - If `WARDEN_ALLOWED_PROJECTS` is set (check via `_resolve_allowed_projects(root, load_env(...))[1] == ".env override"`), `out.warn` that the **`WARDEN_ALLOWED_PROJECTS` override** (env or `.env`) currently shadows `warden.toml`, so the change won't take effect until that var is cleared. (Wording: say "WARDEN_ALLOWED_PROJECTS override", not just ".env" — it may be a process env var.)
   - A running warden won't pick this up until restart → `out.info("run `catraz reload` to apply to a running stack")` (ties into item 02).
   - `return EXIT_OK`.
5. **cli.py** — `pa = sub.add_parser("allow", parents=[_g()], help="add GitLab project(s) to the warden allowlist")`;
   `pa.add_argument("projects", nargs="+", help="full project path(s), e.g. group/sub/project")`;
   `HANDLERS["allow"] = setup.cmd_allow`.
6. **README** — add `| catraz allow <path>… | Append project(s) to the warden allowlist |`
   to the Basic-commands table; mention the wizard now offers detected GitLab remotes.

## Success criteria
- `pytest tests/cli/test_allow.py` green, covering:
  - `_project_from_remote_url`: HTTPS w/ + w/o `.git`, SSH `git@host:grp/proj.git`,
    nested `group/sub/proj`, a non-matching host → `None`, a self-hosted `gitlab_url`
    host match, and a URL that derives an invalid path → `None`.
  - `merge_allowed`: drops `""` placeholder, dedupes, preserves order.
  - `cmd_allow`: appends to a tmp `warden.toml` seeded with the real default
    `allowed_projects = []` (and one extra case seeded with `[""]` to pin the defensive
    drop), rejects a wildcard entry (warns, still writes the valid ones), is idempotent on
    a repeat, and warns when `WARDEN_ALLOWED_PROJECTS` is set.
  - `_discover_gitlab_projects` (smoke, `skipif` no `git`): a real `git init` + `git remote
    add origin https://gitlab.com/grp/proj.git` + a non-gitlab remote → returns `["grp/proj"]`.
- A wizard run in a repo with a `gitlab.com` remote pre-fills that project as the default.

## Risks & open questions
- Multiple repos under the init folder: scan `root` + immediate git subdirs only (bounded);
  deeper trees are out of scope. Document the one-level depth.
- Self-hosted GitLab on a non-standard path/port: host-only match (ignore port) keeps it
  simple; sub-path installs (`https://host/gitlab/group/proj`) are not handled — note as a
  known limitation, the user can still type the path by hand.
- "Offer, never auto-add" is honored: discovery only sets the prompt **default**; the user
  must accept (Enter) or edit. `-y` mode never discovers.

## Revision history
- v0: initial draft
- v1 (roast, shared agent): accepted the factual correction — the shipped placeholder is
  `allowed_projects = []`, not `[""]` (the `policy.py` docstring is stale); fixed the prose
  + test fixtures and kept the empty-string drop as defensive only. Spelled out the new
  imports for `_wizard_interactive.py` and `cmd_allow` (the setup module imports no policy
  symbols today). Pinned the SSH-form parser to NOT use `urllib.parse`, clarified `root` is
  always scanned regardless of the subdir cap, and reworded the override warning to name
  `WARDEN_ALLOWED_PROJECTS`. Roast verdict: mergeable.
