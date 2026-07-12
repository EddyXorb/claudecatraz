# 02 — init-egress-offer

Derives from [`../14-egress-allowlist-from-manifest.md`](../14-egress-allowlist-from-manifest.md)
§2 and [`../05-agent-layer.md`](../05-agent-layer.md) §05.4. On contradiction
the main doc wins — report it, do not guess. Depends on
[01-egress-allowlist-model](01-egress-allowlist-model.md). Security-sensitive:
the never-auto-merge and decline-writes-nothing tests land in this commit.

## How

* **`src/catraz/commands/setup/_wizard_interactive.py`**: add
  `_prompt_egress_offer(root: Path, cat: Path, out: Out) -> None`, called once
  from `_wizard_interactive` after the existing prompts (near the closing
  summary), interactive path only. `_wizard_yes.py` gets no equivalent call —
  `--yes` never offers or adds domains, matching every other non-interactive
  guarantee in that module.
* Resolve `profile = resolve_agent_profile(root)` and
  `manifest = load_manifest(profile)` (`catraz.agents`). If
  `manifest.egress_domains` is empty, or `cat / "config" / "allowlist.txt"`
  does not exist, return without prompting.
* Read the current allowlist text once; compute `candidates = [d for d in
  manifest.egress_domains if not domain_covered(text, d)]`
  (`catraz.egress_allowlist`). Empty `candidates` → return silently (nothing
  to offer — matches the shipped `claude` profile's domains being pre-covered
  by the shipped baseline today).
* **Out-of-tree gate**: if `profile not in SHIPPED_PROFILES`
  (`catraz.agents`), print the full candidate list as a diff (`+ <domain>`
  per line) and require one explicit yes/no confirmation for the whole set
  before any per-domain prompt runs; declining returns immediately, allowlist
  untouched. Shipped profiles skip straight to the per-domain offer.
* **Per-domain offer**: for each candidate, `out.ask(f"allow {domain}?",
  "n")`-style prompt (same "y"/"n" idiom as `_prompt_configure_endpoint`);
  build `confirmed = existing ∪ {accepted candidates}` where `existing =
  agent_block(text, profile) or ()` — so a previously-confirmed domain that
  is still in the manifest and still uncovered stays confirmed without
  re-asking, while one the operator hand-deleted from the block falls out of
  `existing`, becomes a fresh candidate, and requires a fresh yes.
* If nothing was confirmed this run and `existing` is also empty, write
  nothing. Otherwise write once via
  `allowlist_path.write_text(upsert_agent_block(text, profile, tuple(confirmed)))`.
* Print a one-line summary (`• N domain(s) allowed for profile 'X'` /
  `• no domains added`) — same style as the existing wizard's closing info
  block.

## Tests

Extend `tests/cli/test_init_wizard.py` or add `tests/cli/test_init_egress_offer.py`:
* Shipped profile, an uncovered candidate domain, answer "y": the domain lands
  inside `# agent:claude begin/end` and nowhere else in the file.
* Same setup, answer "n" (or EOF/default): `allowlist.txt` byte-identical to
  before the wizard ran.
* `--yes` path: `allowlist.txt` byte-identical to before `cmd_init`, even with
  a manifest that has non-empty `egress_domains` — the never-auto-merge
  guarantee.
* Out-of-tree profile (monkeypatch `catraz.agents.SHIPPED_PROFILES` to a set
  excluding a test profile, or register a second `AGENT_REGISTRY` entry
  pointing at a fixture `agent.toml`): the per-domain prompts are never
  reached without the diff confirmation being accepted first; declining the
  diff confirmation writes nothing.
* Re-running the wizard after a hand-edit that deletes one domain from inside
  an existing `# agent:<profile>` block: that domain is offered again (not
  silently restored) on the next run.
