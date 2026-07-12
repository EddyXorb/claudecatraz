# 14 — Manifest egress domains: guided allowlist offer and provenance

An agent profile declares the domains it needs to reach in `agent.toml`
(`[egress].domains`), parsed into `AgentManifest.egress_domains`
(`catraz.agents.load_manifest`). The forward proxy enforces egress against a
separate, hand-maintained Squid allowlist (`assets/config/allowlist.txt`, mounted
read-only into the `forward-proxy` service). 05-agent-layer §05.4 fixed the
relationship between the two: the manifest domains are a **suggestion**, never
merged into the allowlist automatically — that merge is the one real
exfiltration boundary, exactly where an `evil.com` would try to hide.

The **decision** is recorded (§05.4, and the `agent.toml` comment points at it).
The **flow it describes is not built**: `egress_domains` is parsed and then read
nowhere, `catraz init` never offers the domains, and `catraz doctor` never prints
the effective egress list. This document records that gap and the guided flow
that closes it. It is *what/why* only; no implementation is scheduled.

> Prerequisite: the agent-layer manifest (§05.3) and the forward proxy
> (agentic-workflow/02-forward-proxy). This is a flagged follow-up, not part of
> the init/doctor cleanup (11).

---

## 1. What is missing today

* **`egress_domains` is dead config.** `AgentManifest.egress_domains` is parsed
  from `[egress].domains` and never consumed — a grep for its use finds only the
  parse site and a test. A profile can declare any domains it likes with no
  effect on what the proxy allows.
* **The allowlist is hand-maintained.** `assets/config/allowlist.txt` is a static
  shipped asset. Adding a domain the agent needs (for example the Anthropic and
  `claude.ai`/`claude.com` hosts under its `# --- Agent runtime ---` block) is a
  manual edit, and today it is duplicated by hand into the manifest's
  `[egress].domains` as well — two places to keep in sync, neither driving the
  other.
* **No provenance.** `catraz doctor` does not surface which domains are allowed or
  where each came from, so an operator cannot see the effective egress boundary
  or how it was assembled without reading the file.

The result is that the boundary §05.4 cares most about is edited by hand with no
guardrail — the opposite of the guided, marked, auditable act it intended.

## 2. The target — offer, mark, and attribute; never auto-merge

The manifest domains stay a suggestion; the work is to make confirming them a
guided, visible act and to make the effective boundary inspectable. Auto-merging
`egress_domains` into the allowlist is **not** the target and stays rejected
(§05.4) — the whole point is that a domain reaching the proxy allowlist is a
human-confirmed step, never a silent consequence of a profile file.

* **`catraz init` offers each manifest domain individually.** The wizard shows
  the profile's `egress_domains` and the operator confirms them one by one (or as
  a diff for a whole block). Confirmed domains land in the allowlist inside a
  marked, commented block — `# agent:<profile>` — so a manifest-sourced entry is
  always visibly attributed and separable from operator-added lines. Declining
  leaves the allowlist untouched.
* **A profile outside the shipped assets** (a fork or a custom adapter) requires
  an explicit confirmation at `init` that shows the diff of its egress domains
  before any are written — an untrusted manifest cannot slip a domain past the
  operator even into the offer.
* **`catraz doctor` prints the effective egress list with provenance** — each
  allowed domain and whether it came from the shipped baseline, an
  `# agent:<profile>` block, or a manual operator edit — so the exfiltration
  boundary is auditable from one command.
* The `# agent:<profile>` block is regenerated/updated on confirmation, never
  silently on every run; a domain the operator deleted by hand is not re-added
  without a fresh confirmation.

With this in place `egress_domains` stops being dead config: it is the source the
init offer reads and the doctor attributes, and the manual double-entry into both
the manifest and the allowlist goes away.

## 3. Alternatives considered

* **Auto-merge `egress_domains` into the allowlist.** The obvious wiring, and the
  one §05.4 explicitly forbids: it turns profile data into egress policy with no
  human step, so a compromised or careless manifest widens the exfiltration
  boundary invisibly. Rejected — it is the anti-goal, not a shortcut to it.
* **Leave it hand-maintained (today).** Zero code, but the boundary is edited
  with no guardrail, `egress_domains` stays dead, and the manifest/allowlist
  duplication persists. Rejected as the end state.
* **Generate the allowlist wholly from manifests at build time.** Would make the
  file a pure derivative, but erases the operator's own hand-added lines and the
  shipped baseline (package registries, toolchain) unless those become manifests
  too — a larger model change for no gain over an attributed block. Rejected.

## 4. Scope note

This is catraz operator-surface work (`init` wizard, `doctor`, and the allowlist
writer), not a warden or proxy change — Squid keeps enforcing the file as-is;
only how the file is assembled and shown changes. It is independent of 12 and 13.
Until it lands, the allowlist stays hand-maintained and `egress_domains` stays
unconsumed; the manual edit is the current workaround.

## 5. Conventions for the implementation

Author and committer are the repo identity **EddyXorb**; no co-authorship or
"generated with" trailers, no mention of AI tools anywhere. One commit per step,
each leaving the full verification green (`uv run --with pytest python -m pytest
tests/cli tests/container -q`, `uv run mypy`). The security-relevant tests land
with the change: a shipped-profile domain is offered and, once confirmed, written
only inside its `# agent:<profile>` block; an out-of-tree profile is not offered
without the explicit diff confirmation; nothing is written on decline; and
`doctor` reports each domain's provenance. Docstrings and comments follow
`docs/RULES.md`: short, no history, no obvious statements, no markup, and no
references to files under `docs/design/`.
