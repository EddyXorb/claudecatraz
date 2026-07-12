# 14 — Manifest egress domains: guided allowlist offer and provenance — implementation steps

These derive from the main document
[`../14-egress-allowlist-from-manifest.md`](../14-egress-allowlist-from-manifest.md)
(the *what/why*) and the binding decision in
[`../05-agent-layer.md`](../05-agent-layer.md) §05.4; each step's *how* is its
own file beside this index. On contradiction the main document wins — report
it, do not guess.

## Order and progress

The number is the dependency level: same number = independent (parallelizable,
separate commit); a higher number requires all lower ones. Flip the Status
column in the same commit as the step.

| Level | Step | Status |
| --- | --- | --- |
| 01 | [egress-allowlist-model](01-egress-allowlist-model.md) | ☑ |
| 02 | [init-egress-offer](02-init-egress-offer.md) | ☑ |
| 02 | [doctor-egress-provenance](02-doctor-egress-provenance.md) | ☐ |

Level 01 is the pure model (`catraz.egress_allowlist`): parsing, the
`# agent:<profile>` block writer, the coverage check, and the provenance
classifier — no CLI wiring. Both level-02 steps only read that module; they
touch disjoint files (`_wizard_interactive.py` vs `doctor.py`) and can be
built/reviewed/committed in either order.

## Verification (every step)

```
uv run --with pytest python -m pytest tests/cli tests/container -q
uv run mypy
```

## Hard rules

Identity **EddyXorb**, no AI/tool mentions anywhere. One commit per step,
green each time. `egress_domains` is never auto-merged into the allowlist —
every write is downstream of an explicit operator confirmation, and the
security-relevant test lands in the same commit as the change it covers:
a shipped-profile domain is offered and, once confirmed, written only inside
its `# agent:<profile>` block; an out-of-tree profile is not offered without
the explicit diff confirmation; nothing is written on decline; `doctor`
reports each domain's provenance correctly. Docstrings/comments per
`docs/RULES.md` (≤5/≤2 lines, no `docs/design` or `§` references in code
prose). Keep the Squid egress allowlist (`allowlist.txt`) and the warden
project allowlist (`warden.toml allowed_projects`) distinct in naming,
functions, and doctor sections — never share an identifier between them.

## Scope note — the shipped baseline stays

The main doc §2 foresees the manifest/allowlist "double-entry" going away. This
plan keeps the shipped `# --- Agent runtime ---` baseline (`.anthropic.com`,
`.claude.ai`, `.claude.com`) in `assets/config/allowlist.txt` rather than
retiring it in favour of the offer. Retiring it would leave a bare
`catraz init --yes` (which never offers or adds egress domains) shipping with
the agent unable to reach the model at all — a narrowing of the default that
must be a named, deliberate decision, not a side effect of this work. So the
offer is purely additive: for the shipped `claude` profile every manifest
domain is already covered by the baseline, making the offer a present-day
no-op; the mechanism is exercised by any later or out-of-tree domain the
baseline does not already cover. Fully collapsing the overlap (retiring the
baseline, or narrowing it to what the manifest declares) is left as a separate,
explicitly-confirmed change.
