# Rules

Implicit conventions of this repo, made explicit. Keep each rule short; add
new ones here the moment they become a convention.

## The action model (one sentence)

Every request maps, via recognizers, to a set of actions; it proceeds iff
every action is below IRREVERSIBLE in criticality and enabled for the target
host; everything else is denied.

## Docstrings

- Never reference `docs/design/` paths — code explains itself.
- No history ("formerly", "was called X", design-doc section numbers, commits).
- Never state the obvious.
- As short as possible; only the surprising, load-bearing facts.
- No Sphinx roles (`:class:`, `:func:`, …), no heavy markup — plain prose
  that reads well without an IDE.

## Commits & MRs

- Author/committer is the repo identity **EddyXorb**.
- No co-authorship or "Generated with" trailers; no mention of AI assistants
  or tools anywhere — commits, branches, MR/PR titles and descriptions.
- One commit per plan step; every commit leaves the full verification green.
- Behavior is proven by tests, never by inspection.

## Security posture

- Fail closed: unknown ids, unknown field values, unmatched requests →
  deny / `ConfigError`. Allowlists of known-safe, never blocklists.
- Effective config is built once at startup — no runtime reload.
- Never widen behavior in a refactor; intentional widenings are named in a
  design doc first.

## Code layout & naming

- `core/` never imports `guards/*`; core treats action ids as opaque strings.
- Vocabularies are closed sets: no wildcards, no inheritance, no add/remove —
  config lists replace completely; a missing key ≠ an empty list.
- Actions say *what*; scope (namespace/allowlist), quota, and token/mode are
  separate axes — never encoded in action ids.
- Actions are transport-independent: same effect ⇒ same action id on every
  wire.
- Guard constants like `SUPPORTED` are accessed qualified
  (`gitlab.actions.SUPPORTED`), never imported bare.
- "Endpoint" means one configured host + type — nothing else.

## Design docs

- Main doc = *what/why*; step subfolder = *how*, derived from it. On
  contradiction the main doc wins — report, don't guess.
- Step files carry numeric prefixes (`01-`, `02-`, …): the number is the
  dependency level — same number = independent, parallelizable; a higher
  number requires all lower ones. `00-index.md` gives the overview and tracks
  progress.
