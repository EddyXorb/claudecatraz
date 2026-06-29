# .catraz — this sandbox's configuration

Everything that defines one catraz sandbox lives here, in three tiers.

## Yours — edit freely
- `.env` — build & wiring (auth mode, GitLab URL, optional `BASE_*` overrides)
- `config/image/Dockerfile` — the base image (`FROM …`); catraz layers Node +
  Claude Code on top. Must be Debian/Ubuntu-based.
- `config/warden.toml` — GitLab policy (allowed projects, limits)
- `config/squid.conf`, `config/allowlist.txt` — egress-proxy rules
- `secrets/` — your credentials (GitLab tokens, Anthropic key, Claude login).
  Mode 0700, never commit.

## Output — read, don't edit
- `compose.resolved.yml` — the complete docker-compose catraz actually runs,
  fully interpolated. Regenerated on every state-changing command; host- and
  version-specific (not portable). Read it to see exactly what runs: mounts,
  networks, and secrets (as file references, never values). Hand edits are
  overwritten.
- `logs/` — audit trail: proxy egress, warden git activity, agent transcripts.

## Internal — leave alone
- `state/` — warden database and runtime socket.
