# 10 — Parameter rework (C1–C6, C9, C10): de-hardcode the runtime/CLI seams

**Goal:** remove the hardcoded-but-should-be-configurable seams found in the configurability
audit. Two are user-facing gaps (no shell into the container; the remote-control invocation is
fixed), four are correctness/consistency fixes, and two are safe coupling clean-ups.

**Scope decision:** C7 (`/workspace` single-source) and C8 (`dev`-user single-source) are
**dropped** — they are internal constants no user changes, and the de-dup blast radius
(Dockerfile + compose + entrypoint + run.py + tests) is disproportionate to a drift that has
never occurred. This doc realizes **C1–C6, C9, C10**.

**Out of scope (deliberately):** the Tier-3 "knobs" (theme, log level, timeouts, image prefix,
…) and every security/structural invariant — including `--permission-mode bypassPermissions`
itself (kept hardcoded; see C2), the tmpfs `0700` shadow, the `http_proxy`/`no_proxy` egress
routing, `agent-net internal`, `no-new-privileges`, squid `safe_ports 80 443`, the service-name
map, and `--profile remote`.

**Preconditions:** current `feat/packaging` (post-CLI-rework: CLI under `src/catraz/commands/`,
container entrypoint at `src/catraz/assets/container/entrypoint.py`, compose at
`src/catraz/assets/compose/docker-compose.yml`).

**Conventions (inherit `00-overview.md`):** Python ≥ 3.11, stdlib-only for `catraz`; one commit
per "Commit 10.x" block; `uv run --with pytest python -m pytest tests/ -q` green before each
commit (redteam tests skip without a Docker daemon — fine); warden tests green after any warden
change; Conventional-Commits subject, **no trailers**; work on `feat/packaging`, never `main`.

## Cross-cutting mechanism (important for CI)

User-tunable values live in `.catraz/.env`. `compose.base_cmd` already passes
`--env-file .catraz/.env`, so compose **interpolates** `${VAR:-default}` from there with no code
change — only runtime-computed values (`PROJECT_DIR`, `CATRAZ_ASSETS`) need process injection in
`compose.run`. So C2/C10 add **no** new injection; they add `${VAR:-default}` references in the
compose file and `environment:` pass-throughs where the value must reach the agent container.

**The `compose-validate.yml` `env-coverage` gate** scans every `${VAR}` / `${VAR:-default}` in
the compose file and fails if it is not present in `.env.example` (commented lines count). So
**every new compose variable must be added to `.env.example`** (commented-out is fine). This
caught us before with `CATRAZ_ASSETS`; do not repeat it. New vars introduced here:
`CLAUDE_RC_SPAWN`, `CLAUDE_RC_DEBUG_FILE`, `CLAUDE_RC_EXTRA_ARGS`, `SQUID_PORT` — all must appear
(commented) in `.env.example`.

---

## Commit 10.1 — C1: `catraz shell` (run an arbitrary command / a shell in the sandbox)

**Problem:** the container entrypoint always `execvp("claude", …)`, so `catraz run -- bash` runs
`claude bash`. There is no way to get a shell (or run any non-claude command) inside the sandbox.

**Solution — a new entrypoint subcommand `exec` + a new `catraz shell` command.** Same one-off
`docker compose run --rm` path as `run`, same lazy-infra + trust-boundary preflight, but it execs
the given command (default `bash`) as the `dev` user **without** launching claude.

**`entrypoint.py`** — add an `exec` subcommand:
```python
def cmd_exec(cmd: list[str]) -> None:
    drop_to_dev()                       # chowns /workspace + re-execs as dev (as in start/run)
    argv = cmd or ["bash"]
    os.execvp(argv[0], argv)
```
`cmd_exec` deliberately does **not** call `build_home`/`configure_git_warden` — an ad-hoc shell
doesn't need a provisioned claude home or the git rewrite, and skipping them keeps it fast and
side-effect-free. It **does** call `drop_to_dev()`, which (as for `start`/`run`) chowns
`/workspace` to `dev` and re-execs, so the shell lands as `dev` in a writable workspace even on a
cold one-off. In `main()`:
```python
ex = sub.add_parser("exec"); ex.add_argument("rest", nargs=argparse.REMAINDER)
...
if args.command == "exec":
    rest = args.rest[1:] if args.rest[:1] == ["--"] else args.rest
    cmd_exec(rest); return
```

**`src/catraz/commands/run.py`** — generalize the one-off builder (single, final signature) and
add `cmd_shell`. The new signature is `(relpath, tty, sub, sub_args)`:
```python
def _oneoff_args(relpath: str, tty: bool, sub: str, sub_args: list[str]) -> list[str]:
    args = ["run", "--rm", "--no-deps"]
    if not tty:
        args.append("-T")
    args += ["--workdir", f"/workspace/{relpath}".rstrip("/"),
             "claude-dev-env", sub, "--", *sub_args]
    return args

def cmd_shell(root, args, out):
    assert_real_dirs(root); auth.write_auth_fragment(root); assert_invariants(root)
    _ensure_infra(root, out)
    relpath = str(Path.cwd().resolve().relative_to(root)); relpath = "" if relpath == "." else relpath
    tty = sys.stdin.isatty()
    cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd      # may be empty → entrypoint runs bash
    run_args = _oneoff_args(relpath, tty, "exec", cmd)
    extra_env = {"BASE_IMAGE": image.resolve_base(root)}
    r = compose_run(root, run_args, check=False, extra_env=extra_env)
    return r.returncode if r else EXIT_GENERAL
```
- `cmd_run` is updated to call `_oneoff_args(relpath, tty, "run", claude_args)` (the only call-site
  change). **`tests/cli/test_run.py` is updated in this same commit** to the new signature — both
  existing calls become `_oneoff_args("…", tty=…, sub="run", sub_args=[…])`. Without this the
  commit lands red.
- Interactive `catraz shell` needs a TTY; `docker compose run` allocates one by default unless
  `-T`. Keep the existing `-T`-when-not-a-tty rule (so piped use still works). We do **not** inject
  `-it` (compose run is interactive by default for a TTY stdin).

**`cli.py`** — register the command:
```python
psh = sub.add_parser("shell", help="open a shell (or run a command) inside the sandbox")
psh.add_argument("cmd", nargs=argparse.REMAINDER, help="command to run (default: bash)")
...
HANDLERS["shell"] = run_cmd.cmd_shell
```

**Tests** `tests/cli/test_shell.py` (no Docker) — assert by tail position, not substring search:
```python
from catraz.commands import run as run_cmd
def test_shell_oneoff_default_bash():
    a = run_cmd._oneoff_args("", tty=False, sub="exec", sub_args=[])
    assert a[:4] == ["run","--rm","--no-deps","-T"]
    assert a[-3:] == ["claude-dev-env","exec","--"]    # empty → entrypoint defaults to bash
def test_shell_oneoff_passthrough():
    a = run_cmd._oneoff_args("src", tty=True, sub="exec", sub_args=["ls","-la"])
    assert "-T" not in a and "/workspace/src" in a
    assert a[-5:] == ["claude-dev-env","exec","--","ls","-la"]
```
`tests/container/test_exec_mode.py` (path-loaded entrypoint, like the existing entrypoint tests):
monkeypatch `ep.os.execvp` + `ep.drop_to_dev`; assert `cmd_exec([])` execs `bash` and
`cmd_exec(["ls","-la"])` execs `["ls","-la"]`.

**README:** document `catraz shell` next to `catraz run` (run = drop-in claude; shell = arbitrary
command / debugging; own one-off container, same sandbox).

`commit: "feat(cli): catraz shell — run an arbitrary command/shell in the sandbox"`

## Commit 10.2 — C2: parameterize the remote-control invocation (not the permission mode)

**Problem:** `cmd_start` hardcodes `--spawn same-dir`, `--debug-file <home>/rc-debug.log`, and
allows no extra flags.

**Solution — read `--spawn`/`--debug-file`/extra-args from the environment, current values as
defaults. The permission mode stays hardcoded `bypassPermissions`** (it is a keep-fixed
security/operational invariant: a non-bypass mode hangs the headless daemon, and a shell-injected
override would be a free DoS — so it is intentionally *not* exposed). In `entrypoint.py`:
```python
import shlex
def cmd_start(claude_home: Path) -> None:
    drop_to_dev()
    mode = os.environ.get("AUTH_MODE") or "subscription"
    if mode == "api_key" and not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("error: api_key mode but ANTHROPIC_API_KEY unset")
    build_home(claude_home, mode)
    configure_git_warden()
    spawn = os.environ.get("CLAUDE_RC_SPAWN") or "same-dir"
    debug = os.environ.get("CLAUDE_RC_DEBUG_FILE") or str(claude_home / "rc-debug.log")
    extra = shlex.split(os.environ.get("CLAUDE_RC_EXTRA_ARGS") or "")
    os.execvp("claude", ["claude", "remote-control",
                         "--permission-mode", "bypassPermissions",   # keep-fixed (headless)
                         "--spawn", spawn, "--debug-file", debug, *extra])
```
- `or` (not `get(…, default)`) so an empty string falls back to the default.
- With nothing set, argv is byte-identical to today (regression-guarded by test).
- These reach the agent container via compose `environment:` pass-throughs with defaults:
  `- CLAUDE_RC_SPAWN=${CLAUDE_RC_SPAWN:-same-dir}`, `- CLAUDE_RC_DEBUG_FILE=${CLAUDE_RC_DEBUG_FILE:-}`,
  `- CLAUDE_RC_EXTRA_ARGS=${CLAUDE_RC_EXTRA_ARGS:-}` on `claude-dev-env`.
- **`.env.example`**: add a commented "Agent runtime (advanced)" block with all three
  (`# CLAUDE_RC_SPAWN=same-dir`, `# CLAUDE_RC_DEBUG_FILE=`, `# CLAUDE_RC_EXTRA_ARGS=`) — required so
  the `env-coverage` CI gate passes.

**Tests** `tests/container/test_rc_args.py`: monkeypatch `ep.os.execvp`, `ep.drop_to_dev`,
`ep.build_home`, `ep.configure_git_warden`; (a) no env set → argv equals today's literal list
(regression guard); (b) `CLAUDE_RC_SPAWN=project-dir` + `CLAUDE_RC_EXTRA_ARGS="--foo bar"` →
reflected in argv; assert `--permission-mode bypassPermissions` is **always** present and never
read from env.

`commit: "feat(entrypoint): env-driven remote-control spawn/debug/extra flags"`

## Commit 10.3 — C3: make the `AUTH_MODE` default consistent (and fix the empty-value case)

**Problem:** `auth.auth_mode()` and the entrypoint default an unset `AUTH_MODE` to `"subscription"`,
but `doctor.check_auth` (`doctor.py:231`, `mode = env.get("AUTH_MODE", "")`) treats both *absent*
**and** *empty* (`AUTH_MODE=`) as a hard `bad`. Divergence: one path runs subscription, the other
errors.

**Solution — one rule everywhere: empty/absent → `subscription`; only a present-but-invalid value
errors.**
- `doctor.check_auth`: `mode = env.get("AUTH_MODE") or "subscription"`; `if mode not in
  ("subscription","api_key"): f.bad(...); return`. (So `AUTH_MODE=` and a missing key both resolve
  to subscription, matching `auth.py` and the entrypoint.)
- `entrypoint.cmd_start`/`cmd_run`: change `os.environ.get("AUTH_MODE", "subscription")` →
  `os.environ.get("AUTH_MODE") or "subscription"` so an empty env var also resolves to subscription
  (today it would fall through as `""`). `build_home` already keys off `mode == "subscription"`, so
  this fixes a latent empty-value bug there too.
- `auth.auth_mode()` is already `env.get("AUTH_MODE", "subscription")` then validates — keep it as
  the canonical rule; add a one-line comment in `check_auth` pointing at it so they can't drift.

**Tests** extend `tests/cli/test_auth.py`: `check_auth` with `AUTH_MODE` **absent** (subscription
setup, cred present) → no `bad` (was a `bad`); with `AUTH_MODE=""` present → no `bad`; with
`AUTH_MODE="bogus"` → `bad`; api_key with key set → ok.

`commit: "fix(auth): unify AUTH_MODE default (subscription) across auth, doctor, entrypoint"`

## Commit 10.4 — C4: prompt for and surface `GITLAB_URL`

**Problem:** `GITLAB_URL` defaults to `https://gitlab.com` in `doctor.py` (`_probe_gitlab_tokens`,
:149) and `entrypoint.py:81`. Self-hosted users who forget it get token probes against gitlab.com
and a git `insteadOf` rewrite for the wrong host — silently. `catraz init` never prompts; doctor
never surfaces it.

**Solution:**
- **New `doctor.check_gitlab(env, f)`** (section `"tokens"`, so `DOCTOR_SECTIONS` is unchanged),
  dispatched in `run_doctor` right before `check_tokens`:
  ```python
  def check_gitlab(env, f):
      url = (env.get("GITLAB_URL") or "").strip()
      if not url:
          f.warn("tokens", "GITLAB_URL unset — defaulting to https://gitlab.com",
                 "set GITLAB_URL in .catraz/.env for self-hosted GitLab")
      else:
          f.ok("tokens", f"GitLab endpoint: {url}")
  ```
  Runs unconditionally (unlike `check_tokens`, which early-returns when a secret is missing), so the
  endpoint is always visible.
- **`commands/setup.py` `cmd_init`:** after the secrets loop, add a **non-secret** prompt
  (`input()`, not `getpass`) for the GitLab base URL, pre-filled with the current value (default
  `https://gitlab.com`); write `GITLAB_URL` to `.catraz/.env` if changed. Skipped under `--yes`
  (keeps the existing value), like the other prompts.
- **`.env.example`:** relocate `GITLAB_URL` up next to the tokens with a "set this for self-hosted
  GitLab" comment.
- **Drive-by fix:** `doctor.py:134` says `"all three secrets are set"` but `SECRETS` has **two**
  entries (the `ANTHROPIC_API_KEY` prompt is commented out). Correct the text to `"both GitLab
  tokens are set"`.

**Tests** `tests/cli/test_doctor_gitlab.py`: `check_gitlab` with `GITLAB_URL` set → an `ok` item in
section `tokens` whose message contains the URL; unset/empty → a `warn` item. (Init prompting is
interactive — not unit-tested here; the `--yes` keep-existing path is covered by the existing init
behavior.)

`commit: "feat(cli): prompt for GITLAB_URL in init; doctor surfaces the endpoint"`

## Commit 10.5 — C5: honor the shell env for `CLAUDE_CREDENTIAL_SOURCE`

**Problem:** `commands/setup.py:_run_sync` (line 166) resolves `src = source or
env.get("CLAUDE_CREDENTIAL_SOURCE")` — only the `.catraz/.env` value, never the process env. The
container entrypoint (`entrypoint.py:29`) honors `os.environ` first. So a user exporting
`CLAUDE_CREDENTIAL_SOURCE` in their shell finds it ignored by `catraz sync`.

**Solution:** match the entrypoint's precedence (`import os` is already present):
```python
src = (source
       or os.environ.get("CLAUDE_CREDENTIAL_SOURCE")
       or env.get("CLAUDE_CREDENTIAL_SOURCE"))
```
(`--from` > shell env > `.env` value > entrypoint default `~/.claude`.) This also flows through the
auto-sync path (`_auto_sync_if_needed` → `_run_sync` with `source=None`) with no extra change.

**Tests** `tests/cli/test_sync_source.py`: monkeypatch **`catraz.commands.setup.subprocess.run`**
(the module that actually calls it — not `cli.subprocess`) to capture the command and return
`returncode 0`; monkeypatch `paths.asset_root` to a temp dir holding a stub `entrypoint.py`. (a)
`CLAUDE_CREDENTIAL_SOURCE` set in `os.environ`, none in `.env` → passed as `--from`; (b) `--from`
flag overrides the shell env.

`commit: "fix(cli): _run_sync honors CLAUDE_CREDENTIAL_SOURCE from the shell env"`

## Commit 10.6 — C6: configurable / XDG-aware asset cache location

**Problem:** `paths.asset_root()` hardcodes the cache root at `Path.home()/".cache"/…`.
Restricted-home / CI environments cannot relocate it and `$XDG_CACHE_HOME` is ignored.

**Solution — precedence `CATRAZ_CACHE_DIR` > `$XDG_CACHE_HOME` > `~/.cache`:**
```python
def _cache_root() -> Path:
    base = (os.environ.get("CATRAZ_CACHE_DIR")
            or os.environ.get("XDG_CACHE_HOME")
            or str(Path.home() / ".cache"))
    return Path(base).expanduser() / "catraz" / __version__
```
`asset_root()` uses `_cache_root()` for `dst`. With neither env var set this is byte-identical to
today. `CATRAZ_CACHE_DIR` is an env-only escape hatch (governs extraction *before* a project
exists); documented in the README install section, not in `.catraz/.env`.

**Tests** `tests/cli/test_paths.py` — **all `Path.home`-monkeypatching tests must also clear the
env** or they break on any developer machine that has `XDG_CACHE_HOME` set:
add `monkeypatch.delenv("CATRAZ_CACHE_DIR", raising=False)` and
`monkeypatch.delenv("XDG_CACHE_HOME", raising=False)` to the three existing tests
(`test_asset_root_extracts`, `test_asset_cache_refreshes_on_source_change`,
`test_asset_cache_stable_without_change`). Add new cases: `CATRAZ_CACHE_DIR=<tmp>` →
`<tmp>/catraz/<v>`; only `XDG_CACHE_HOME=<tmp>` → `<tmp>/catraz/<v>`.

`commit: "feat(paths): CATRAZ_CACHE_DIR / XDG_CACHE_HOME for the asset cache"`

## Commit 10.7 — C9: admin socket path from a single source

**Problem:** `/run/warden/admin.sock` is the warden `ADMIN_UDS` env **and** a duplicated literal in
the healthcheck command (`docker-compose.yml:34`). Change `ADMIN_UDS` and the healthcheck silently
keeps probing the old path.

**Solution — the healthcheck reads the env var (with a safe default, never bare `[]`):**
```yaml
healthcheck:
  test: ["CMD","python3","-c","import os,socket;socket.socket(socket.AF_UNIX).connect(os.environ.get('ADMIN_UDS','/run/warden/admin.sock'))"]
```
`ADMIN_UDS` (already set in the warden `environment:`) is now the single source of truth on the
container side; `os.environ.get(…, default)` avoids a `KeyError` if it is ever unset. The volume
mount dir `/run/warden` is its parent. No new variable, no behavior change. Add a compose comment
that `ADMIN_UDS` is authoritative and the host-side CLI path (`.catraz/run/warden/admin.sock` in
`observe.py`/`doctor.py`) mirrors it by contract.

**Tests:** none (compose-only; `docker compose config` CI validates syntax). Manual: `catraz up`
healthcheck still passes.

`commit: "refactor(compose): healthcheck derives admin socket from ADMIN_UDS"`

## Commit 10.8 — C10: squid port single source (compose-only)

**Problem:** `3128` is duplicated in `squid.conf` (`http_port 3128`), the compose forward-proxy
healthcheck (`/dev/tcp/127.0.0.1/3128`), and the four agent proxy env vars
(`http://forward-proxy:3128`).

**Solution — compose-only single source (the roast's recommended, safe option).** Do **not**
runtime-`sed` the security-sensitive squid config (that risked dropping the `ssl-bump tls-cert=…`
suffix and breaking `squid-ci.yml`). Instead:
- `docker-compose.yml`: healthcheck `echo > /dev/tcp/127.0.0.1/${SQUID_PORT:-3128}`; the four proxy
  env vars `http://forward-proxy:${SQUID_PORT:-3128}` (http/https, lower/upper). These interpolate
  from `--env-file .catraz/.env`, so setting `SQUID_PORT` there changes all five at once.
- `squid.conf`: leave `http_port 3128 ssl-bump tls-cert=…` unchanged, with a comment: *"if you
  change this port, also set `SQUID_PORT` in `.catraz/.env` to match."* The remaining duplication is
  two places (squid.conf ↔ `.env`), down from three, documented, and a port change is rare.
- **`.env.example`:** add a commented `# SQUID_PORT=3128` (required for the `env-coverage` gate).

Default `3128` reproduces today's behavior exactly; `squid-ci.yml` (which runs squid with the
bundled config and never sets `SQUID_PORT`) stays green untouched.

**Tests:** `docker compose config` CI covers interpolation; `env-coverage` covers the `.env.example`
entry. No code/runtime change to the proxy.

`commit: "refactor(compose): single-source the squid port via SQUID_PORT (compose-only)"`

---

## Acceptance (Doc 10)
- `uv run --with pytest python -m pytest tests/ -q` green; warden tests green; all compose-touching
  CI (`compose-validate` incl. `env-coverage`, `squid-ci`, `redteam`) green.
- `catraz shell` opens a bash shell in the sandbox; `catraz shell -- ls -la /workspace` runs a
  command and exits with its code (manual, Docker host). `catraz run` unchanged (drop-in claude).
- Overriding `CLAUDE_RC_SPAWN`/`CLAUDE_RC_DEBUG_FILE`/`CLAUDE_RC_EXTRA_ARGS` changes the daemon
  invocation; unset → identical to today; `--permission-mode bypassPermissions` is always present
  and never env-driven.
- Deleting **or** blanking `AUTH_MODE` no longer produces a doctor error in subscription mode;
  a bogus value still errors.
- `catraz init` prompts for the GitLab URL; `catraz doctor` shows the effective endpoint (and warns
  when unset); the stale "all three secrets" text is fixed.
- `CLAUDE_CREDENTIAL_SOURCE` exported in the shell is honored by `catraz sync`.
- `CATRAZ_CACHE_DIR` / `XDG_CACHE_HOME` relocate the asset cache; unset → `~/.cache/catraz`; the
  `Path.home` tests clear those env vars so they're stable on XDG machines.
- C9/C10 behavior byte-identical at defaults; each value now has a single authoritative source.
- All commits authored as the repo owner, no trailers.
