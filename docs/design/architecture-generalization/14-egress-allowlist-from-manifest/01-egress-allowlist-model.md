# 01 — egress-allowlist-model

Derives from [`../14-egress-allowlist-from-manifest.md`](../14-egress-allowlist-from-manifest.md)
§2. On contradiction the main doc wins — report it, do not guess. Pure model,
no CLI wiring: everything here is a function over text, testable without a
filesystem or a wizard.

## How

* **New module** `src/catraz/egress_allowlist.py`. Operates on the Squid
  allowlist text (`.catraz/config/allowlist.txt` format: `#`-comment lines +
  one `.domain`/`domain` entry per line) — never touches `warden.toml
  allowed_projects`, a separate allowlist with its own module (`policy.py`).
* **Block markers.** A profile's confirmed domains live between two comment
  lines: `# agent:<profile> begin` and `# agent:<profile> end`. Only text
  between them is machine-managed; everything else (shipped baseline lines,
  other profiles' blocks, manual lines) is preserved byte-for-byte.
* `agent_block(text: str, profile: str) -> tuple[str, ...] | None` — the
  domains currently inside *profile*'s marked block, in file order, or `None`
  if the block does not exist yet (distinct from an empty confirmed set).
* `upsert_agent_block(text: str, profile: str, domains: tuple[str, ...]) ->
  str` — replaces the existing block's contents in place, or appends a new
  block (blank line + header + entries + footer) at end-of-file if none
  exists yet, or removes the block entirely when *domains* is empty. Content
  outside the markers is never rewritten (no reflow, no reordering).
* `domain_covered(text: str, domain: str) -> bool` — `True` if *domain*
  already appears verbatim anywhere in *text* (any provenance), or is a
  subdomain of an existing `.domain` entry. Used to skip offering a domain
  that is already reachable, so a shipped baseline (e.g. `.anthropic.com`)
  makes the same manifest domain a silent no-op instead of a redundant
  `# agent:<profile>` entry that would trip Squid's own redundancy warning.
* `DomainEntry` — frozen dataclass, `entry: str`, `provenance: str` (one of
  `"baseline"`, `f"agent:{profile}"`, `"manual"`).
* `classify_domains(text: str, baseline_text: str) -> tuple[DomainEntry,
  ...]` — walks *text* line by line: lines inside an `# agent:<profile>`
  block get that provenance; a domain line outside any block gets
  `"baseline"` if the identical line appears (outside any block) in
  *baseline_text* (the shipped `assets/config/allowlist.txt`), else
  `"manual"`.
* **`catraz.agents`**: add `SHIPPED_PROFILES: frozenset[str] = frozenset({"claude"})`,
  next to `AGENT_REGISTRY`. Deliberately *not* derived from `AGENT_REGISTRY` —
  a fork that registers an additional profile there leaves it out of this set,
  which is what makes step 02's diff-confirmation gate fire for it.

## Tests

New `tests/cli/test_egress_allowlist.py`:
* `upsert_agent_block` on a fixture allowlist text: first call appends a new
  block leaving prior content untouched; a second call with different domains
  replaces only the block's interior (position-stable, no reordering of
  content outside it); an empty-domains call removes the block cleanly.
* `domain_covered`: exact match, `.domain` suffix match, and a negative case
  (no match) — including against the shipped `assets/config/allowlist.txt`
  fixture itself (`.anthropic.com` covers `api.anthropic.com`).
* `classify_domains` against the shipped baseline: every shipped line reports
  `"baseline"`; a line inside a synthesized `# agent:claude` block reports
  `"agent:claude"`; a hand-appended line outside any block reports
  `"manual"`.
* `agent_block` returns `None` before any confirmation and the confirmed
  tuple after `upsert_agent_block`, in file order.
