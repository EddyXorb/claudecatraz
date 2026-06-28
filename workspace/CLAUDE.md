# Project CLAUDE.md (placeholder)

This file is **project-specific** and belongs to the repo mounted at `/workspace`
(build commands, code style, test commands for this project). In real use,
`PROJECT_DIR` mounts the respective GitLab clone here and this template is
replaced by the project's own CLAUDE.md.

**Sandbox/harness knowledge** (network, egress, forward proxy, Warden, GitLab API)
does NOT live here — it is injected by the container from the image as user memory
(`~/.claude/CLAUDE.md`, source: `AGENT.md` in the repo root). This keeps it
project-independent and prevents it from being accidentally committed to a project repo.
