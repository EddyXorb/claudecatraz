# Background agent-image build (P1)

> Status: implemented · Topic: cli-worklist · Iterations planned: 1

## Problem
`catraz run` (and `run claude-remote`) regularly stalls for minutes with no visible
progress. Cause, per start:

1. `image.resolve_claude_code_version` asks the npm registry for the latest
   `@anthropic-ai/claude-code` (blocking, ≤5 s timeout) and passes the concrete version
   as the `CLAUDE_CODE_VERSION` build arg.
2. Compose is invoked with `run --build` / `up --build`, so every start runs a build.
3. Claude Code publishes roughly daily. A new version changes the build arg; because
   `ARG CLAUDE_CODE_VERSION` is declared before the single monolithic `RUN` in
   `agents/claude/layer.Dockerfile` (apt-get update + apt install + NodeSource setup +
   nodejs + `npm install -g`), the cache of that whole layer and everything after it
   is invalidated. The first start of the day re-downloads the apt/node toolchain and
   the CLI: typically 1–4 minutes, longer on slow links.
4. `--quiet-build` hides the build output, so the stall looks like a hang.

## Goal
A start never waits for a *Claude Code version bump*. It uses the last successfully
built image immediately; a detached worker checks the registry and builds the new
version in the background; the next start picks it up atomically.

## Model
- **Recipe** = everything except the Claude Code version that determines the agent
  image: agent profile, `BASE_IMAGE` (resolved tag), `NODE_VERSION`, `DEV_UID`, and the
  bytes of the build-context files the layer copies (`agents/<profile>/*`,
  `container/*`, the layer Dockerfile itself). Hashed to 12 hex chars.
- **Image tag** = `<compose-project>-agent:<recipe>-<version>` — unique per
  (recipe, version), so a build never overwrites the image a running container uses.
- **Pointer** `.catraz/image/agent.json` = `{tag, recipe, version}` of the last
  successful build. Written with `os.replace` (atomic). Compose consumes it via
  `image: ${AGENT_IMAGE}` + `pull_policy: never`; the agent service has no `build:`
  section, and `run`/`up` no longer pass `--build`. catraz itself is the only builder.

## Start flow (`agent_image.ensure`)
1. Compute the recipe; read the pointer.
2. **Foreground build** (visible progress, one-line reason up front) when:
   - no pointer or its image is gone (first run, `docker image prune`),
   - recipe differs (catraz upgrade, Dockerfile/entrypoint/adapter change, other base,
     node, uid, profile),
   - `CLAUDE_CODE_VERSION` is pinned and differs from the pointer's version.
3. Otherwise use the pointer's tag at once, print a status line if relevant (background
   build running / last background build failed / image switched since last start), and,
   unless the version is pinned, spawn the detached worker.

### Why a recipe change must build in the foreground
The image carries the container half of the host/container contract: `entrypoint.py`,
`agent_contract.py`, `git_routing.py`, the adapter and manifest. The CLI renders compose
mounts, env and auth wiring for *its* version of that contract. Running an older image
under a newer CLI is a skew that can fail open or break in odd ways, so correctness wins
over latency here. Only the Claude Code CLI version — which the contract does not
depend on — is allowed to lag one start behind.

## Background worker (`catraz build --background`, hidden)
- Spawned detached (new session / detached process group, stdin closed, output to
  `.catraz/logs/image-refresh.log`), so it survives the parent and never writes to the
  user's terminal.
- Lock `.catraz/image/build.lock`, created with `O_CREAT|O_EXCL` (portable, no `fcntl`);
  a lock older than 2 h counts as stale. A held lock means another worker is active →
  exit. The lock body records the version being built for the status line.
- Exits early when the pointer's recipe differs from the current one (the foreground
  path owns that case) or the registry is unreachable.
- Same version as the pointer → nothing to do. Else `docker build` into the new tag with
  `--progress=plain` to `.catraz/logs/image-build.log`.
- Success: re-read the pointer and switch only if its recipe is still the one built
  (a concurrent upgrade's foreground build wins); mark it as new for a one-time
  "switched" notice; best-effort `docker image rm` of the project's other agent tags
  (in-use images are refused by docker and stay).
- Failure: write `.catraz/image/last-failure.json`; the next start shows one warning line
  with the log path. The old image stays in use. Any later success clears it.

## Explicit rebuild
`catraz build [--no-cache]`: foreground, resolves the latest (or pinned) version, builds
with visible progress and switches the pointer. Refuses while a background build holds
the lock (it would race the pointer).

## Dockerfile
`ARG CLAUDE_CODE_VERSION` moves directly above a separate `RUN npm install -g …`, so a
version bump only rebuilds that one layer; apt and NodeSource stay cached. This shrinks
every rebuild (background, foreground and explicit) to roughly the npm install.

## Alternatives considered
- **Keep `--build`, just show progress**: honest, but still minutes per day of waiting.
- **Throttle the registry check (e.g. once a day)**: still blocks on the day it fires.
- **Install Claude Code at container start** (entrypoint `npm install`): needs egress
  for npm on every start, mutates the image at runtime, slower each run.
- **Retag a fixed `:latest`** instead of a pointer file: `docker tag` is atomic too, but a
  file lets the CLI know the recipe/version without `docker inspect` and works the same
  for every compose project; the unique per-build tag keeps running containers' images
  intact.
- **`fcntl.flock`**: cleaner release on crash, but POSIX-only; catraz also runs on Windows.

## Open points
- A mutable `BASE_IMAGE` tag (e.g. `ubuntu:24.04` upstream) is not re-pulled
  automatically; `docker pull <base>` followed by `catraz build` rebuilds on top of it.
- No throttle on the registry check — one small HTTPS request per start from the
  detached worker.
