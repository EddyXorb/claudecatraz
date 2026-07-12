#!/usr/bin/env python3
"""Generic container entrypoint — and host-side credential sync tool.
Agent-agnostic: UID drop, tmpfs-home lifecycle, shadow-mount contract,
proxy env, git→Warden routing, process exec. Agent-specific behavior is
delegated to the adapter this image was built for, via `agent_contract.py`."""

import argparse
import importlib.util
import os
import sys
import tomllib
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_contract import AgentAdapter, InstructionContext, Secrets  # noqa: E402
from git_routing import configure_git_warden, install_host_gitconfig  # noqa: E402


def _env_true(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


# Host-persistent target for --debug-file output; bind-mounted so debug logs
# survive container exit even when the live agent home is a tmpfs (sync mode).
AGENT_LOG_DIR = Path("/var/log/agent-debug")


def resolve_log_dir(home: Path) -> Path:
    """Prefer the host-persistent bind when present and writable by the dev
    user (doctor creates + chowns it); fall back to the ephemeral tmpfs home
    otherwise (bare `docker run` for local testing, stale root-owned dir)."""
    if AGENT_LOG_DIR.is_dir() and os.access(AGENT_LOG_DIR, os.W_OK):
        return AGENT_LOG_DIR
    return home


def _load_adapter() -> AgentAdapter:
    """The one adapter this image was built for (co-located next to this
    file) — no dynamic selection at runtime; the build already committed to
    exactly one agent."""
    p = Path(__file__).resolve().parent / "agent_adapter.py"
    spec = importlib.util.spec_from_file_location("agent_adapter", p)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # see agent_contract.py's Secrets/InstructionContext
    spec.loader.exec_module(mod)
    return cast(AgentAdapter, mod)


# ── host-side sync (credentials.mode = "sync" only) ──────────────────────────


def cmd_sync(adapter: AgentAdapter, home: Path, source: str | None = None) -> None:
    sync = getattr(adapter, "sync_from_host", None)
    if sync is None:
        sys.exit(
            "error: this agent profile has no host-credential sync "
            "(credentials.mode=persistent logs in from inside the container instead)"
        )
    sync(Path(source).expanduser() if source else None, home)


# ── generic per-start setup ───────────────────────────────────────────────────


def drop_to_dev() -> None:
    """If running as root, fix /workspace ownership and re-exec as the dev user via gosu."""
    if os.getuid() != 0:
        return
    import pwd

    try:
        pw = pwd.getpwnam("dev")
    except KeyError:
        sys.exit("error: user 'dev' not found in container")

    workspace = Path("/workspace")
    if workspace.exists():
        os.chown(workspace, pw.pw_uid, pw.pw_gid)

    os.execvp("gosu", ["gosu", "dev", sys.executable] + sys.argv)


def install_instructions(adapter: AgentAdapter, ctx: InstructionContext) -> None:
    """Write the agent's rendered instructions file (target and content).
    Fails closed when REQUIRE_AGENT_INSTRUCTIONS is set and rendering
    doesn't produce anything (packaging error), otherwise starts without
    instructions (e.g. a bare `docker run` for local testing)."""
    try:
        dest, content = adapter.render_instructions(ctx)
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see the exit message
        if _env_true("REQUIRE_AGENT_INSTRUCTIONS"):
            sys.exit(f"error: could not render agent instructions: {exc}")
        return
    if not content and _env_true("REQUIRE_AGENT_INSTRUCTIONS"):
        sys.exit("error: REQUIRE_AGENT_INSTRUCTIONS is set but rendered instructions are empty")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content)


def _read_branch_prefixes(warden_toml_path: Path) -> tuple[str, ...]:
    """Best-effort `branch_prefixes` (or the scalar `branch_prefix`)
    read from the mounted warden.toml, for the rendered instructions'
    example only. A missing/unreadable/malformed file degrades to the
    `claude/` default rather than crashing the entrypoint before the agent
    starts."""
    try:
        data: dict[str, Any] = tomllib.loads(warden_toml_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return ("claude/",)
    val = data.get("branch_prefixes")
    if isinstance(val, list) and val and all(isinstance(p, str) for p in val):
        return tuple(val)
    scalar = data.get("branch_prefix")
    if isinstance(scalar, str) and scalar:
        return (scalar,)
    return ("claude/",)


def _instruction_context() -> InstructionContext:
    warden_toml_path = Path("/etc/catraz/warden.toml")
    return InstructionContext(
        # Generic per-host rule, not one concrete URL: "<host>" is a placeholder
        # the agent substitutes with whichever git host it's talking to.
        forge_rest_base=os.environ.get("WARDEN_REST_URL", "http://<host>:8080/api/v4"),
        branch_prefixes=_read_branch_prefixes(warden_toml_path),
        warden_toml_path=warden_toml_path,
    )


def _resolve_secrets(home: Path, *, remote: bool) -> Secrets:
    mode = os.environ.get("AUTH_MODE") or "subscription"
    ro = home / ".ro"
    api_key_file_env = os.environ.get("ANTHROPIC_API_KEY_FILE")
    return Secrets(
        auth_mode=mode,
        subscription_ro_dir=ro if ro.is_dir() else None,
        api_key_file=Path(api_key_file_env) if api_key_file_env else None,
        api_key_env_fallback=os.environ.get("ANTHROPIC_API_KEY", ""),
        remote=remote,
    )


def _bootstrap(adapter: AgentAdapter, home: Path, *, remote: bool) -> None:
    """Shared per-start setup for every container entry mode (start/run/exec):
    drops root to dev, resolves secrets and hands them to the adapter,
    rebuilds the live home, and routes git through the warden."""
    drop_to_dev()
    secrets = _resolve_secrets(home, remote=remote)
    os.environ["AGENT_LOG_DIR"] = str(resolve_log_dir(home))
    try:
        os.environ.update(adapter.environ(secrets))
    except Exception as exc:  # noqa: BLE001 - fail closed with the adapter's own message
        sys.exit(f"error: {exc}")
    adapter.prepare_home(home, secrets)
    install_host_gitconfig(home)
    configure_git_warden()
    install_instructions(adapter, _instruction_context())


def cmd_exec(adapter: AgentAdapter, home: Path, cmd: list[str]) -> None:
    """Interactive shell / one-off command in the sandbox (`catraz run shell`):
    runs the full bootstrap so the home and git-warden rewrite are in place,
    same as a one-off/remote run. remote=False — not the remote-control
    daemon."""
    _bootstrap(adapter, home, remote=False)
    argv = cmd or ["bash"]
    os.execvp(argv[0], argv)


def cmd_start(adapter: AgentAdapter, home: Path) -> None:
    _bootstrap(adapter, home, remote=True)
    argv = adapter.remote_command()
    if argv is None:
        sys.exit(
            "error: this agent profile does not support remote-control mode (modes.remote=false)"
        )
    # flush=True: execvp replaces this process image, so anything still buffered
    # in Python's stdio would never reach the container's stdout.
    print(f"[entrypoint] remote-control daemon exec: {' '.join(argv)}", flush=True)
    os.execvp(argv[0], argv)


def cmd_run(adapter: AgentAdapter, home: Path, argv: list[str]) -> None:
    _bootstrap(adapter, home, remote=False)
    full = adapter.command(argv)
    os.execvp(full[0], full)


# ── CLI ───────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    default_home = os.environ.get("AGENT_HOME", str(Path.home() / "agent-home"))
    parser.add_argument(
        "--agent-home",
        default=default_home,
        help="agent config directory [env: AGENT_HOME]",
    )
    sub = parser.add_subparsers(dest="command")

    sync = sub.add_parser("sync", help="Import host credentials into --agent-home")
    sync.add_argument(
        "--agent-home",
        default=default_home,
        help="Target directory [env: AGENT_HOME]",
    )
    sync.add_argument(
        "--from",
        dest="source",
        default=None,
        help="Source credential dir (adapter-specific default env var, if any)",
    )

    rn = sub.add_parser("run")
    rn.add_argument("rest", nargs=argparse.REMAINDER)  # ["--", "<args>"...]

    ex = sub.add_parser("exec")
    ex.add_argument("rest", nargs=argparse.REMAINDER)

    args = parser.parse_args()
    adapter = _load_adapter()

    if args.command == "sync":
        cmd_sync(adapter, Path(args.agent_home).resolve(), source=args.source)
        return
    if args.command == "run":
        rest = args.rest[1:] if args.rest and args.rest[0] == "--" else args.rest
        cmd_run(adapter, Path(args.agent_home).resolve(), rest)
        return
    if args.command == "exec":
        rest = args.rest[1:] if args.rest[:1] == ["--"] else args.rest
        cmd_exec(adapter, Path(args.agent_home).resolve(), rest)
        return
    cmd_start(adapter, Path(args.agent_home).resolve())


if __name__ == "__main__":
    main()
