import contextlib
import io
import os
from pathlib import Path

from catraz.agents import effective_credentials_mode, load_adapter_module, resolve_agent_profile
from catraz.envfile import load_env
from catraz.errors import CliError, EXIT_GENERAL
from catraz.paths import claude_home
from catraz.ui import Out


def _credentials_mode(root: Path) -> str:
    """Effective credentials mode ("sync"|"persistent"): `.catraz/.env`'s
    CLAUDE_CREDENTIALS_MODE overrides the manifest default, so sync-gating
    agrees with the compose overlay and the adapter. Falls back to "sync" if
    unresolvable — a doctor `agent` finding surfaces that separately."""
    try:
        return effective_credentials_mode(root)
    except CliError:
        return "sync"


def _ensure_gitignore(root: Path) -> None:
    """Append a `.catraz/` line to <root>/.gitignore (create if missing), once."""
    gi = root / ".gitignore"
    lines = gi.read_text().splitlines() if gi.exists() else []
    if any(ln.strip() == ".catraz/" for ln in lines):
        return
    with gi.open("a") as fh:
        if lines and lines[-1].strip():
            fh.write("\n")
        fh.write(".catraz/\n")


def _run_sync(
    root: Path,
    out: Out,
    source: str | None = None,
    force: bool = False,
    quiet: bool = False,
) -> None:
    """Host-side credential import (`catraz sync`) — only meaningful for
    `credentials.mode = "sync"`; persistent profiles log in from inside
    the container instead, so this refuses rather than importing
    credentials nothing will use. Runs the resolved profile's
    `sync_from_host` in-process, no container needed."""
    if _credentials_mode(root) == "persistent":
        raise CliError(
            "this agent profile uses credentials.mode=persistent — "
            "`catraz sync` does not apply; run `claude login` inside the "
            "container instead (state persists in .catraz/state/<profile>/)",
            EXIT_GENERAL,
        )
    profile = resolve_agent_profile(root)
    adapter = load_adapter_module(profile)
    sync = getattr(adapter, "sync_from_host", None)
    if sync is None:
        raise CliError(f"agent profile {profile!r} has no host-credential sync", EXIT_GENERAL)

    env = load_env(root / ".catraz" / ".env")
    home = claude_home(root)
    src = (
        source or os.environ.get("CLAUDE_CREDENTIAL_SOURCE") or env.get("CLAUDE_CREDENTIAL_SOURCE")
    )
    src_path = Path(src).expanduser() if src else None
    try:
        if quiet:
            with contextlib.redirect_stdout(io.StringIO()):
                sync(src_path, home)
        else:
            sync(src_path, home)
    except SystemExit as e:
        raise CliError(str(e.code) or "credential sync failed", EXIT_GENERAL)


def _auto_sync_if_needed(root: Path, out: Out) -> None:
    """Subscription: keep the sandbox seed credential as fresh as the host,
    on a best-effort basis. The in-container home is tmpfs so refreshes
    never flow back; this re-copies host→sandbox on every cold start,
    strictly one-way. Can't help if both host and seed are dead (needs an
    interactive host `claude` login)."""
    if load_env(root / ".catraz" / ".env").get("AUTH_MODE", "subscription") != "subscription":
        return
    if _credentials_mode(root) == "persistent":
        return  # persistent profiles have nothing to sync from the host
    had = (claude_home(root) / ".credentials.json").exists()
    if not had:
        out.info("• subscription credential missing — attempting sync…")
    try:
        _run_sync(root, out, quiet=had)
    except CliError as e:
        if not had:
            out.warn(str(e) + " — run `catraz sync` once authenticated")
