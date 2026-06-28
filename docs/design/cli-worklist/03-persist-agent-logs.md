# Persist agent run transcripts to `.catraz/logs/agent` (P10)

> Status: draft · Topic: cli-worklist · Iterations planned: 1

## Goal
A **non-interactive** `catraz run` one-off (`docker compose run --rm`, e.g. `catraz run
-p "…"` or piped) loses its stdout/stderr when it exits — the output lives only in
Docker's json-file driver on a now-removed container. Tee the agent's output to a durable
per-run file under `.catraz/logs/agent/<timestamp>.log`, next to the existing
`logs/warden` and `logs/squid`. Interactive TTY runs are explicitly out of scope (see
Risks) — capturing a live pty/TUI is deferred.

## Context / constraints
- `catraz run` → `run.py:cmd_run` → `compose.run(root, run_args, prefix=, check=False)`;
  `run_args` from `_oneoff_args(relpath, tty, "run", claude_args)` which adds `-T`
  (no pseudo-TTY) **only when `tty` is False** (`run.py:12-22`).
- **The agent cannot write the log itself.** `/workspace/.catraz` is a tmpfs shadow inside
  the agent container (`docker-compose.yml:141-143`, asserted by `assert_invariants`), so
  an in-container redirect to `.catraz/logs/agent` is invisible to the host. Teeing must
  happen **host-side, in the CLI**.
- Interactive TTY runs allocate a real pty (no `-T`); teeing a live TUI would both fight
  the pty and capture escape-code noise. Non-TTY runs (`-T`, e.g. `catraz run -p "…"`,
  piped) produce clean pipe output that tees cleanly.
- The remote daemon (`up -d`) already has durable logs via Docker's json-file driver
  (`catraz logs agent`); it is **not** in scope here.
- `compose.run` already centralizes cmd + env construction; extend it rather than
  duplicating the `docker compose` invocation in `run.py`.

## Approach
Scope the transcript to **non-TTY `catraz run`** (the case that is both lossy and clean).
Add a `tee` option to `compose.run` that streams the child's combined output to both the
parent fd and a file. In `cmd_run`, when not a TTY, compute a timestamped path under
`.catraz/logs/agent/`, prune old transcripts, and pass it as `tee`. TTY runs are
unchanged. The agent's exit code must still propagate unchanged.

## Steps
1. **compose.py `run()`** — add keyword `tee: Path | None = None`. When `tee` is set and
   not `print_only`:
   - `assert not check, "tee path does not support check="` (the only caller passes
     `check=False`; fail loud if a future caller forgets — the tee branch can't honor it).
   - `tee.parent.mkdir(parents=True, exist_ok=True)`.
   - `with open(tee, "wb") as f:` (binary — `Popen` without `text=True` yields bytes):
     `p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)`.
   - Loop `for chunk in iter(lambda: p.stdout.read1(65536), b""):` — **`read1`, not
     `read`**: `BufferedReader.read(n)` blocks until `n` bytes or EOF, so a chatty run
     under the buffer size would show nothing until exit. `read1` returns as soon as data
     is available, preserving live output. Each chunk: `sys.stdout.buffer.write(chunk);
     sys.stdout.buffer.flush()` (drains the whole chunk, unlike a possibly-short
     `os.write(1, …)`) **and** `f.write(chunk)`.
   - `p.wait()`; `return subprocess.CompletedProcess(cmd, p.returncode)`.
   - Wrap the `FileNotFoundError` (docker missing) the same way the existing path does.
   - Leave behavior identical to today when `tee is None`. `tee` is keyword-only (after
     the existing `*`), so no existing caller is affected.
2. **run.py** — add `import datetime` and a helper
   `def _prune_agent_logs(log_dir: Path, keep: int = 50) -> None`: list `log_dir.glob("*.log")`
   sorted by name (fixed-width timestamps sort chronologically), `p.unlink(missing_ok=True)`
   all but the newest `keep` (missing_ok → safe under concurrent runs).
3. **run.py `cmd_run`** — after computing `tty`:
   - If `tty`: unchanged (`compose_run(..., check=False)` then propagate `returncode`).
   - Else: `log_dir = root/".catraz/logs/agent"`; `log_dir.mkdir(parents=True, exist_ok=True)`;
     `log_path = log_dir / (datetime.datetime.now().strftime("%Y%m%dT%H%M%S_%f") + ".log")`
     (**`%f` microseconds** so two runs in the same second don't collide/clobber);
     `_prune_agent_logs(log_dir)`; `r = compose_run(root, run_args, prefix=prefix, check=False, tee=log_path)`;
     return `r.returncode if r else EXIT_GENERAL`.
   - `cmd_shell` is **not** changed (shells are interactive; out of scope).
4. **README** — under "Interactive mode", note that **non-interactive** `catraz run`
   writes a transcript to `.catraz/logs/agent/<timestamp>.log` (newest 50 kept). State
   plainly that the transcript also contains `docker compose` orchestration noise
   (every one-off runs with `--build`, and stderr is merged into stdout), so it is a
   raw session record, not a clean agent-only log.

## Success criteria
- `pytest tests/cli/test_agent_logs.py` green, covering:
  - `_prune_agent_logs`: with 53 dummy `*.log` files, keeps exactly the 50 newest by name.
  - `cmd_run` non-TTY (monkeypatch `run_cmd.sys.stdin.isatty` → False, stub
    `compose_run` to record kwargs): a `tee` path is passed, and it is under
    `<root>/.catraz/logs/agent` ending in `.log`.
  - `cmd_run` TTY (isatty → True): `compose_run` is called with **no** `tee` (or
    `tee is None`).
  - (compose) `run(root, ["bash","-c","printf hello"], prefix=[], tee=path)` writes
    `hello` into the file and returns `returncode == 0`. **`prefix=[]` is mandatory** —
    an omitted prefix defaults to the `docker compose …` source-cmd, which would shell
    out to docker; an empty (non-None) prefix makes `cmd == args`. Mark `skipif` if
    `bash` is unavailable.
- A real `catraz run -p "say hi"` leaves a non-empty `.catraz/logs/agent/*.log`.

## Risks & open questions
- Combining stdout+stderr (STDOUT redirect) loses the stream distinction and folds in
  `docker compose` build/orchestration chatter — one readable raw record, acceptable for
  a per-run transcript and documented as such.
- **Interactive TTY runs get no transcript** — the common terminal case. Justified
  (teeing a live pty/TUI fights the pty and captures escape-code noise); explicit future
  work would use a `pty`/`script`-based capture. Goal/Context reworded to say
  "non-interactive" so the scope is honest.
- Retention is count-based (50). A single enormous run can still produce one large file;
  acceptable for now (documented). Revisit with size-based rotation only on a real need.
- Mounting a host log dir into the agent was considered and rejected: it widens the
  agent's host write surface and needs an entrypoint redirect; host-side tee keeps the
  agent unchanged and the trust boundary intact.

## Revision history
- v0: initial draft
- v1 (roast iter 1): accepted both CRITICALs — `read1` instead of `read` (liveness), and
  `prefix=[]` mandatory in the compose tee test (else it shells out to docker). Accepted:
  binary `"wb"` + `sys.stdout.buffer` full-write, `%f` microsecond timestamp,
  `unlink(missing_ok=True)`, `assert not check` guard, and reworded Goal/Context/README to
  scope honestly to non-interactive runs and to admit the transcript carries compose noise.
