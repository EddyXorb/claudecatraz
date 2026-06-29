"""One-off agent run command."""
from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

from catraz.errors import CliError, EXIT_GENERAL, EXIT_DOCTOR
from catraz.compose import run as compose_run, compose_ps, assert_real_dirs, assert_invariants
from catraz import image, compose, auth
from catraz.ui import Out
from catraz.commands.stack import _row_ready, _security_preflight, _wait_healthy, _print_urls
from catraz.commands.setup import _auto_sync_if_needed

MODES = ("claude", "claude-remote", "shell")


def _oneoff_args(relpath: str, tty: bool, sub: str, sub_args: list[str]) -> list[str]:
    # --build rebuilds the agent image when its build context changed (e.g. a new
    # entrypoint subcommand like `exec`). Docker's layer cache makes this a near-instant
    # no-op when nothing changed, so every one-off self-heals against CLI/image skew —
    # otherwise a stale image's entrypoint rejects subcommands the CLI now emits.
    # --quiet-build hides the (usually cached, no-op) build progress dump on every run;
    # the infra "Container … Running" lines still print, so catraz visibly works.
    args = ["run", "--rm", "--no-deps", "--build", "--quiet-build"]
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


def _ensure_infra(root: Path, out: Out, prefix: list[str] | None = None) -> None:
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


def cmd_run(root: Path, args: argparse.Namespace, out: Out) -> int:
    """Dispatch `run [<mode>] [-- <args>]` to one of the named modes.

    The first token, if it names a mode, selects it; otherwise the mode defaults to
    `claude`. `claude_args` is the opaque tail that cli._split_run captured verbatim
    after `run`, so claude's own flags arrive intact (e.g. `run -p x` → ["-p","x"])."""
    raw = list(args.claude_args)
    mode = raw.pop(0) if raw and raw[0] in MODES else "claude"
    # Strip one leading `--` so the explicit-separator forms `run -- -p x` and
    # `run claude -- -p x` also yield ["-p","x"]; _oneoff_args adds its own `--`.
    if raw and raw[0] == "--":
        raw = raw[1:]
    if mode == "claude-remote":
        return _start_remote_daemon(root, args, out)
    sub = "exec" if mode == "shell" else "run"
    return _run_oneoff(root, out, sub, raw)


def _run_oneoff(root: Path, out: Out, sub: str, raw: list[str]) -> int:
    """Shared ephemeral one-off path for `claude` (sub=run) and `shell` (sub=exec)."""
    assert_real_dirs(root)
    extra_env = {"BASE_IMAGE": image.resolve_base(root)}
    prefix = compose.prepare(root, render=True, extra_env=extra_env)
    assert_invariants(root, prefix=prefix)
    _ensure_infra(root, out, prefix=prefix)
    relpath = str(Path.cwd().resolve().relative_to(root))
    if relpath == ".":
        relpath = ""
    tty = sys.stdin.isatty()
    run_args = _oneoff_args(relpath, tty, sub, raw)
    if sub == "run" and not tty:
        # Non-TTY claude one-off: stdout is lost when the --rm container is removed, so
        # tee the combined output to a durable per-run transcript (item 03). %f
        # microseconds so two runs in the same second don't clobber each other. TTY runs
        # allocate a real pty (teeing would fight it) and shell is interactive → no tee.
        log_dir = root / ".catraz/logs/agent"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / (datetime.datetime.now().strftime("%Y%m%dT%H%M%S_%f") + ".log")
        _prune_agent_logs(log_dir)
        r = compose_run(root, run_args, prefix=prefix, check=False, tee=log_path)
    else:
        r = compose_run(root, run_args, prefix=prefix, check=False)
    return r.returncode if r else EXIT_GENERAL


def _start_remote_daemon(root: Path, args: argparse.Namespace, out: Out) -> int:
    """Start the Remote-Control agent daemon (ports item 05's removed `up --remote`).

    Unlike the ephemeral `claude`/`shell` one-offs (`run --rm`), this is a long-lived
    daemon (`--profile remote up -d`, restart unless-stopped) — the mode name encodes
    the lifecycle difference the old `run` vs `up --remote` split hid."""
    assert_real_dirs(root)
    (root / ".catraz").mkdir(exist_ok=True)
    auth.write_auth_fragment(root)
    out.head("— preflight (security checks always run) —")
    if _security_preflight(root, out):
        out.err("preflight failed — fix the ✘ above (or `catraz doctor --fix`)")
        return EXIT_DOCTOR
    print()
    _auto_sync_if_needed(root, out)
    extra_env = {"BASE_IMAGE": image.resolve_base(root)}
    prefix = compose.prepare(root, render=True, extra_env=extra_env)
    assert_invariants(root, prefix=prefix)
    out.info("• starting the agent daemon…")
    r = compose_run(root, ["--profile", "remote", "up", "-d"], prefix=prefix, check=False)
    if r and r.returncode == 0:
        _wait_healthy(root, out, prefix=prefix)
        _print_urls(out)
    return r.returncode if r else EXIT_GENERAL
