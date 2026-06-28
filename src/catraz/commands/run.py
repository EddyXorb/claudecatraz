"""One-off agent run command."""
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
    r = compose_run(root, run_args, prefix=prefix, check=False)
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
