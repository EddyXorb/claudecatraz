"""One-off agent run command."""
import sys
from pathlib import Path

from catraz.errors import CliError, EXIT_GENERAL, EXIT_DOCTOR
from catraz.compose import run as compose_run, compose_ps, assert_real_dirs, assert_invariants
from catraz import auth, image
from catraz.commands.stack import _row_ready, _security_preflight
from catraz.commands.setup import _auto_sync_if_needed


def _oneoff_args(relpath: str, tty: bool, claude_args: list[str]) -> list[str]:
    args = ["run", "--rm", "--no-deps"]
    if not tty:
        args.append("-T")
    args += ["--workdir", f"/workspace/{relpath}".rstrip("/"),
             "claude-dev-env", "run", "--", *claude_args]
    return args


def _ensure_infra(root, out):
    """Lazy infra: if warden+squid are already healthy, return fast; otherwise run the
    security preflight + auto-sync, warn about the trust boundary, and start infra only."""
    rows = compose_ps(root)
    healthy = {r.get("Service") for r in rows if _row_ready(r)}
    if {"gitlab-warden", "forward-proxy"} <= healthy:
        return
    if _security_preflight(root, out):
        raise CliError("preflight failed — fix the ✘ above", EXIT_DOCTOR)
    _auto_sync_if_needed(root, out)
    out.warn("catraz: sandbox active (warden+squid) — protects network/git, NOT your files")
    compose_run(root, ["up", "-d"], check=False)


def cmd_run(root, args, out):
    assert_real_dirs(root)
    auth.write_auth_fragment(root)
    assert_invariants(root)                        # ALWAYS, uncached
    _ensure_infra(root, out)                        # lazy: preflight+up only when cold
    relpath = str(Path.cwd().resolve().relative_to(root))
    if relpath == ".":
        relpath = ""
    tty = sys.stdin.isatty()
    # Strip a leading `--` so `catraz run -- -p x` and `catraz run -p x` behave
    # identically; _oneoff_args adds its own `--` separator.
    claude_args = (args.claude_args[1:]
                   if args.claude_args and args.claude_args[0] == "--"
                   else args.claude_args)
    run_args = _oneoff_args(relpath, tty, claude_args)
    extra_env = {"BASE_IMAGE": image.resolve_base(root)}
    r = compose_run(root, run_args, check=False, extra_env=extra_env)
    return r.returncode if r else EXIT_GENERAL
