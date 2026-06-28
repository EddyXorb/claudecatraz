"""One-off agent run command."""
import datetime
import sys
from pathlib import Path

from catraz.errors import CliError, EXIT_GENERAL, EXIT_DOCTOR
from catraz.compose import run as compose_run, compose_ps, assert_real_dirs, assert_invariants
from catraz import image, compose
from catraz.commands.stack import _row_ready, _security_preflight
from catraz.commands.setup import _auto_sync_if_needed


def _oneoff_args(relpath: str, tty: bool, sub: str, sub_args: list[str]) -> list[str]:
    # --build rebuilds the agent image when its build context changed (e.g. a new
    # entrypoint subcommand like `exec`). Docker's layer cache makes this a near-instant
    # no-op when nothing changed, so every one-off self-heals against CLI/image skew —
    # otherwise a stale image's entrypoint rejects subcommands the CLI now emits.
    args = ["run", "--rm", "--no-deps", "--build"]
    if not tty:
        args.append("-T")
    args += ["--workdir", f"/workspace/{relpath}".rstrip("/"),
             "claude-dev-env", sub, "--", *sub_args]
    return args


def _prune_agent_logs(log_dir: Path, keep: int = 50) -> None:
    """Keep only the newest `keep` transcripts. Fixed-width timestamp names sort
    chronologically, so sorting by name == sorting by time. missing_ok makes this
    safe under concurrent runs racing to prune the same files."""
    logs = sorted(log_dir.glob("*.log"))
    for p in logs[:-keep] if keep else logs:
        p.unlink(missing_ok=True)


def _ensure_infra(root, out, prefix=None):
    """Lazy infra: if warden+squid are already healthy, return fast; otherwise run the
    security preflight + auto-sync, warn about the trust boundary, and start infra only."""
    rows = compose_ps(root, prefix=prefix)
    healthy = {r.get("Service") for r in rows if _row_ready(r)}
    if {"gitlab-warden", "forward-proxy"} <= healthy:
        return
    if _security_preflight(root, out):
        raise CliError("preflight failed — fix the ✘ above", EXIT_DOCTOR)
    _auto_sync_if_needed(root, out)
    out.warn("catraz: sandbox active (warden+squid) — protects network/git, NOT your files")
    compose_run(root, ["up", "-d"], prefix=prefix, check=False)


def cmd_run(root, args, out):
    assert_real_dirs(root)
    extra_env = {"BASE_IMAGE": image.resolve_base(root)}
    prefix = compose.prepare(root, render=True, extra_env=extra_env)
    assert_invariants(root, prefix=prefix)
    _ensure_infra(root, out, prefix=prefix)
    relpath = str(Path.cwd().resolve().relative_to(root))
    if relpath == ".":
        relpath = ""
    tty = sys.stdin.isatty()
    # Strip a leading `--` so `catraz run -- -p x` and `catraz run -p x` behave
    # identically; _oneoff_args adds its own `--` separator.
    claude_args = (args.claude_args[1:]
                   if args.claude_args and args.claude_args[0] == "--"
                   else args.claude_args)
    run_args = _oneoff_args(relpath, tty, "run", claude_args)
    if tty:
        # Interactive runs allocate a real pty; teeing would fight it and capture
        # escape-code noise — leave them unchanged (out of scope).
        r = compose_run(root, run_args, prefix=prefix, check=False)
        return r.returncode if r else EXIT_GENERAL
    # Non-TTY one-off: stdout is lost when the --rm container is removed, so tee the
    # combined output to a durable per-run transcript. %f microseconds so two runs in
    # the same second don't clobber each other.
    log_dir = root / ".catraz/logs/agent"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / (datetime.datetime.now().strftime("%Y%m%dT%H%M%S_%f") + ".log")
    _prune_agent_logs(log_dir)
    r = compose_run(root, run_args, prefix=prefix, check=False, tee=log_path)
    return r.returncode if r else EXIT_GENERAL


def cmd_shell(root, args, out):
    assert_real_dirs(root)
    extra_env = {"BASE_IMAGE": image.resolve_base(root)}
    prefix = compose.prepare(root, render=True, extra_env=extra_env)
    assert_invariants(root, prefix=prefix)
    _ensure_infra(root, out, prefix=prefix)
    relpath = str(Path.cwd().resolve().relative_to(root))
    relpath = "" if relpath == "." else relpath
    tty = sys.stdin.isatty()
    cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd      # may be empty → entrypoint runs bash
    run_args = _oneoff_args(relpath, tty, "exec", cmd)
    r = compose_run(root, run_args, prefix=prefix, check=False)
    return r.returncode if r else EXIT_GENERAL
