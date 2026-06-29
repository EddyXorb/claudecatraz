# Sandbox — Context & Rules for the Agent

> This file is the **harness documentation** of the `claude-dev-env` sandbox. catraz
> mounts it read-only from the asset cache into `~/.claude/.ro/CLAUDE.md`, and
> `entrypoint.py` copies it at container start to `~/.claude/CLAUDE.md` (user memory) —
> so it applies to **every** mounted project. It does NOT belong in the project repo;
> project-specific notes go in the CLAUDE.md of the respective `/workspace` project.

You are running as user `dev` in the container `claude-dev-env`. The `/workspace`
folder is a **bind-mount** — the host (VSCode) and the agent share the same
working clone. Every change is immediately visible on the host and vice versa.

## Persistence — `/workspace` is the ONLY durable directory

> **Do all of your work under `/workspace`.** Clone every repository, create every
> file, and run every build there.

`/workspace` is the **only** path backed by the host. The rest of the container
filesystem — including your home directory (`/home/dev`, `~`), `/tmp`, and `/var/tmp`
— is **ephemeral**: it lives only inside this container and is **permanently lost**
the moment the session ends. Anything you `git clone`, write, or build outside
`/workspace` will silently disappear with no way to recover it.

- ✅ `cd /workspace && git clone <url>` — survives, visible on the host.
- ❌ `cd ~ && git clone <url>` or cloning into `/tmp` — gone when the container stops.

When in doubt, run `pwd` and confirm you are under `/workspace` before starting
work. The container always starts you there.

---

## Network & Egress

`agent-net` is `internal: true` — you have **no direct internet route**. Every
outbound request must go through one of the two egress points:

| Destination                  | Route                 | Configuration                                |
| ---------------------------- | --------------------- | -------------------------------------------- |
| Internet (Research, Build)   | Forward-Proxy (Squid) | `http_proxy` / `https_proxy` already set     |
| GitLab                       | Warden (when active)  | `git insteadOf` + `GITLAB_API_URL`           |

**Allowed domains** (short list — full list: `config/allowlist.txt`):
`.anthropic.com`, `.npmjs.org`, `.pypi.org`, `.crates.io`, `files.pythonhosted.org`,
`.conan.io`, `apt.llvm.org`, `sh.rustup.rs`, `static.rust-lang.org`,
`deb.nodesource.com`, `docs.gitlab.com`, `doc.rust-lang.org`, `docs.python.org`,
`stackoverflow.com`, `github.com`, `raw.githubusercontent.com`, `gitlab.com` (interim).

Domains outside the allowlist are **silently blocked** by the proxy (no DNS,
no TCP). Check `logs/squid/access.log` on the host if a request fails.

---

## GitLab — what works, what doesn't

### No token in the container (by design)

You hold **no** GitLab token. This is intentional (security architecture §R6). All
GitLab operations run exclusively through the **Warden** (`gitlab-warden:8080`), which
holds all tokens and enforces the policy.

### GitLab runs through the Warden

`git` is automatically redirected — no difference in usage:

```bash
git clone https://gitlab.com/group/project.git   # transparently routed through the Warden
git fetch && git push origin claude/my-branch     # likewise
```

REST calls (create MR, trigger CI, etc.) directly against the Warden (`gitlab-warden:8080`):

```bash
# Create MR
curl -sS "http://gitlab-warden:8080/api/v4/projects/<id>/merge_requests" \
  -H "Content-Type: application/json" \
  -d '{"source_branch":"claude/my-branch","target_branch":"main","title":"..."}'

# Trigger CI pipeline
curl -sS -X POST "http://gitlab-warden:8080/api/v4/projects/<id>/pipeline" \
  -H "Content-Type: application/json" \
  -d '{"ref":"claude/my-branch"}'
```

The Warden expects **no auth** from the agent — token injection happens internally.

**Responses come back as plain, uncompressed JSON.** The Warden decompresses any
upstream `Content-Encoding` (gzip/deflate) before relaying and strips the header, so
the body always matches the headers — no `--compressed`, no `gunzip`, no
`Accept-Encoding` juggling needed. Just read the body:

```bash
curl -sS "http://gitlab-warden:8080/api/v4/groups/<id>/projects"
```

### Warden not active (stage 01 / no Warden profile)

No token, no Warden → **no write access to GitLab**. Public repos are readable
via the forward proxy (`git clone` / `git fetch`). Push will fail.

### Hard limits (Warden enforces, cannot be bypassed)

| Allowed                                      | Forbidden                                                          |
| -------------------------------------------- | ------------------------------------------------------------------ |
| Push to `claude/*` branches                  | Push to `main`, `develop`, or branches without the `claude/` prefix |
| Create MRs, comment, trigger CI              | Merge MRs (→ 403, always)                                          |
| Read (API GETs, git fetch/clone)             | Read tokens from the environment (none present)                    |
| Up to 5 open MRs at a time                   | More than 60 write actions/hour                                    |

---

## Toolchain

All tools are globally available on `PATH`:

| Tool        | Version (from `.env`)  | Command                                  |
| ----------- | ---------------------- | ---------------------------------------- |
| Clang/LLVM  | `CLANG_VERSION`        | `clang++`, `clang-tidy`, `clang-format`  |
| Rust        | `RUST_VERSION`         | `cargo`, `rustc`, `rustfmt`, `clippy`    |
| Python / uv | `UV_VERSION`           | `python3`, `uv`, `uv run`, `uv sync`     |
| Conan       | `CONAN_VERSION`        | `conan`                                  |
| Node        | `NODE_VERSION`         | `node`, `npm`                            |
| Claude Code | `CLAUDE_CODE_VERSION`  | `claude`                                 |

Build traffic (cargo, pip, npm, conan) routes **automatically** through the forward
proxy — no manual `--proxy` flag needed.

### Branch prefix

All your own branches must start with the value set in WARDEN_BRANCH_PREFIX:

```bash
git checkout -b WARDEN_BRANCH_PREFIX/my-feature
```

Pushes to other branch names will be rejected by the Warden.
