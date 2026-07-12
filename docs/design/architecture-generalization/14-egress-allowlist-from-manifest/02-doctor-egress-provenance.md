# 02 — doctor-egress-provenance

Derives from [`../14-egress-allowlist-from-manifest.md`](../14-egress-allowlist-from-manifest.md)
§2 and [`../05-agent-layer.md`](../05-agent-layer.md) §05.4 ("`catraz doctor`
prints the effective egress list including provenance"). On contradiction the
main doc wins — report it, do not guess. Depends on
[01-egress-allowlist-model](01-egress-allowlist-model.md); independent of
[02-init-egress-offer](02-init-egress-offer.md) (disjoint files, either order).

## How

* **`src/catraz/doctor.py`**: add `check_egress(root: Path, env: dict[str,
  str], f: Findings) -> None`. Missing `.catraz/config/allowlist.txt` →
  `f.bad("egress", "allowlist.txt missing", "run `catraz init`")` and return.
  Otherwise read it, read the shipped baseline
  (`asset_root() / "assets" / "config" / "allowlist.txt"`), call
  `classify_domains(text, baseline_text)` (`catraz.egress_allowlist`), and
  emit one `f.ok("egress", f"{entry.entry} [{entry.provenance}]")` per domain
  in file order. Zero domains → `f.warn("egress", "no domains allowed — the
  agent cannot reach any network host")`.
* Add `"egress"` to `DOCTOR_SECTIONS` (advisory section, like `"agent"` /
  `"endpoints"` — not in `SECURITY_SECTIONS`; Squid is the fail-closed side,
  doctor only surfaces what it already enforces) and wire the dispatch line
  in `run_doctor`.
* Keep this section's naming and messages clearly about the *Squid* egress
  boundary — never reuse the word "allowlist" bare in a way that could be
  read as the warden project allowlist (`check_policy`'s section is
  `"policy"`, already distinct).

## Tests

New `tests/cli/test_doctor_egress.py` (mirrors `test_doctor_agent.py`'s
shape):
* A fresh `.catraz/config/allowlist.txt` (shipped copy, no confirmations
  yet): every reported domain has provenance `"baseline"`.
* After synthesizing an `# agent:claude begin/end` block with one domain
  (via `upsert_agent_block`, not the wizard) and appending one hand-written
  manual line outside any block: `run_doctor(root, only=["egress"])` reports
  exactly those three provenances (`"baseline"` × N, `"agent:claude"` × 1,
  `"manual"` × 1) — correctness of the attribution is the security-relevant
  assertion here (§05.4's "doctor prints the effective egress list with
  provenance").
* Missing `allowlist.txt` → a single `BAD` finding in section `"egress"`.
