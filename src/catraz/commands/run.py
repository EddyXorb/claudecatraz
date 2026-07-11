"""One-off agent run command."""

from __future__ import annotations

import argparse
import datetime
import sys
import time
from pathlib import Path

from catraz.errors import CliError, EXIT_GENERAL, EXIT_DOCTOR
from catraz.compose import (
    run as compose_run,
    compose_ps,
    assert_real_dirs,
    assert_invariants,
)
from catraz import image, compose
from catraz.ui import Out
from catraz.commands.stack import (
    _row_ready,
    _security_preflight,
    _wait_healthy,
    _print_urls,
)
from catraz.commands.setup import _auto_sync_if_needed

MODES = ("claude", "claude-remote", "shell")


def _oneoff_args(relpath: str, tty: bool, sub: str, sub_args: list[str]) -> list[str]:
    # --build self-heals CLI/image skew (near-instant no-op via layer cache when
    # unchanged); --quiet-build hides its progress dump but keeps infra status lines.
    args = ["run", "--rm", "--no-deps", "--build", "--quiet-build"]
    if not tty:
        args.append("-T")
    args += [
        "--workdir",
        f"/workspace/{relpath}".rstrip("/"),
        "claude-dev-env",
        sub,
        "--",
        *sub_args,
    ]
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


def _host_gitconfig_env() -> dict[str, str]:
    p = Path.home() / ".gitconfig"
    return {"HOST_GITCONFIG": str(p)} if p.exists() else {}


def _run_oneoff(root: Path, out: Out, sub: str, raw: list[str]) -> int:
    """Shared ephemeral one-off path for `claude` (sub=run) and `shell` (sub=exec)."""
    assert_real_dirs(root)
    extra_env = {
        "BASE_IMAGE": image.resolve_base(root),
        "CLAUDE_CODE_VERSION": image.resolve_claude_code_version(root),
        **_host_gitconfig_env(),
    }
    prefix = compose.prepare(root, render=True, extra_env=extra_env)
    assert_invariants(root, prefix=prefix)
    _ensure_infra(root, out, prefix=prefix)
    relpath = str(Path.cwd().resolve().relative_to(root))
    if relpath == ".":
        relpath = ""
    tty = sys.stdin.isatty()
    run_args = _oneoff_args(relpath, tty, sub, raw)
    if sub == "run" and not tty:
        # Non-TTY one-off: stdout is lost when the --rm container is removed, so tee
        # output to a durable per-run transcript; %f microseconds avoid same-second clobber.
        log_dir = root / ".catraz/logs/agent"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / (datetime.datetime.now().strftime("%Y%m%dT%H%M%S_%f") + ".log")
        _prune_agent_logs(log_dir)
        r = compose_run(root, run_args, prefix=prefix, check=False, tee=log_path)
    else:
        r = compose_run(root, run_args, prefix=prefix, check=False)
    return r.returncode if r else EXIT_GENERAL


def _start_remote_daemon(root: Path, args: argparse.Namespace, out: Out) -> int:
    """Start the Remote-Control agent daemon.

    Unlike the ephemeral `claude`/`shell` one-offs (`run --rm`), this is a
    long-lived daemon (`--profile remote up -d`, restart unless-stopped)."""
    assert_real_dirs(root)
    (root / ".catraz").mkdir(exist_ok=True)
    out.head("— preflight (security checks always run) —")
    if _security_preflight(root, out):
        out.err("preflight failed — fix the ✘ above (or `catraz doctor --fix`)")
        return EXIT_DOCTOR
    print()
    _auto_sync_if_needed(root, out)
    extra_env = {
        "BASE_IMAGE": image.resolve_base(root),
        "CLAUDE_CODE_VERSION": image.resolve_claude_code_version(root),
        **_host_gitconfig_env(),
    }
    prefix = compose.prepare(root, render=True, extra_env=extra_env)
    assert_invariants(root, prefix=prefix)
    out.info("• starting the agent daemon…")
    # --build self-heals CLI/image skew, same as the one-off path (_oneoff_args) —
    # near-instant no-op via layer cache when the image is already current.
    r = compose_run(
        root,
        ["--profile", "remote", "up", "-d", "--build", "--quiet-build"],
        prefix=prefix,
        check=False,
    )
    if r and r.returncode == 0:
        _print_remote_command(root, out, prefix=prefix)
        _wait_healthy(root, out, prefix=prefix)
        _print_urls(out)
    return r.returncode if r else EXIT_GENERAL


def _print_remote_command(root: Path, out: Out, prefix: list[str] | None, timeout: int = 10) -> None:
    """Surface the exact argv the entrypoint exec'd (logged as `[entrypoint]
    remote-control exec: …`), so a silent `up -d` doesn't hide what's running."""
    marker = "[entrypoint] remote-control daemon exec:"
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = compose_run(root, ["logs", "--no-log-prefix", "claude-dev-env"], prefix=prefix, capture=True, check=False)
        if r and marker in r.stdout:
            line = next(ln for ln in r.stdout.splitlines() if marker in ln)
            out.info(line.removeprefix("[entrypoint] ").strip())
            return
        time.sleep(0.5)
