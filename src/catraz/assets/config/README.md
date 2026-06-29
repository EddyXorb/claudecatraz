# `config/` — host-editable configuration (mounted read-only)

This folder holds the **configurable, non-secret** files of the sandbox. It is mounted
**read-only** into the containers and is intentionally **host-editable** (e.g. in VSCode),
so policy/allowlist can be maintained without rebuilding an image.

**Core rule: NO secret ever lives here.** Tokens/secrets belong exclusively in
`.catraz/secrets/` (one file each, mode 0600, mounted as compose secrets to the authorized
service); non-secret wiring lives in `.catraz/.env`. Rationale and full picture:
`docs/design/agentic-workflow/README.md` §11.

| File | For | Applies in | Docs |
| ---- | --- | ---------- | ---- |
| `allowlist.txt` | Forward-proxy (Squid): allowed domains | stage 02 | `02-forward-proxy/03-squid-config.md` |
| `squid.conf` | Forward-proxy (Squid): filter configuration | stage 02 | `02-forward-proxy/03-squid-config.md` |
| `warden.toml` | Warden: prefix, limits, allowed projects | stage 02 | `02-warden.md` (W10) |

**Versioning:** `config/` is **deliberately committed** (it is the policy artifact). In
contrast, `.env` (non-secret wiring), `secrets/` (tokens) and `state/` / `logs/` (runtime
data) are gitignored.

**Status:** stage 01 (bootstrap hardening) lays down these files as scaffolding; they only
take effect with the stage-02 containers (Warden, forward-proxy).
