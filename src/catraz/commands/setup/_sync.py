import contextlib
import io
import os
from pathlib import Path

from catraz.agents import load_adapter_module, load_manifest, resolve_agent_profile
from catraz.envfile import load_env
from catraz.errors import CliError, EXIT_GENERAL
from catraz.paths import claude_home
from catraz.ui import Out


def _credentials_mode(root: Path) -> str:
    """The active agent profile's `credentials.mode` (§05.6): "sync" or
    "persistent". Falls back to "sync" (today's historical default) if the
    profile/manifest can't be resolved — a doctor `agent` finding surfaces
    that separately; sync-gating degrades to the old behaviour rather than
    silently blocking a broken-but-otherwise-working setup."""
    try:
        return load_manifest(resolve_agent_profile(root)).credentials_mode
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
    `credentials.mode = "sync"` (§05.6). The default profile now defaults to
    "persistent" (its own `claude login` inside the container instead), so
    this refuses with a clear message rather than silently importing
    credentials nothing will use — the least-surprising of the two options
    §05.6 allows ("eine CLI-Option/klare Meldung unterbindet es").

    Runs the resolved profile's `sync_from_host` in-process (§05.2/§05.3) —
    no subprocess/entrypoint.py indirection: unlike the container-side entry
    modes, host-side sync never needs to run inside a container, and the
    adapter is ordinary Python the host can import directly (via
    `catraz.agents.load_adapter_module`, which never reads code from
    `.catraz/`, only the shipped `assets/agents/` tree, §06.2/A2).
    """
    if _credentials_mode(root) == "persistent":
        raise CliError(
            "this agent profile uses credentials.mode=persistent (§05.6) — "
            "`catraz sync` does not apply; run `claude login` inside the "
            "container instead (state persists in .catraz/state/<profile>/)",
            EXIT_GENERAL,
        )
    profile = resolve_agent_profile(root)
    adapter = load_adapter_module(profile)
    sync = getattr(adapter, "sync_from_host", None)
    if sync is None:
        raise CliError(
            f"agent profile {profile!r} has no host-credential sync", EXIT_GENERAL
        )

    env = load_env(root / ".catraz" / ".env")
    home = claude_home(root)
    src = (
        source
        or os.environ.get("CLAUDE_CREDENTIAL_SOURCE")
        or env.get("CLAUDE_CREDENTIAL_SOURCE")
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
    """Subscription: keep the sandbox seed credential as fresh as the host (best-effort).

    The host ~/.claude credential advances whenever Claude refreshes it there; the sandbox
    seed is frozen at sync time (in-container home is tmpfs → refreshes never flow back). So
    on every (cold) start we re-copy host→sandbox: a host that's used now and then keeps the
    seed current with no manual `catraz sync`. Strictly one-way; the untrusted agent never
    writes toward the host. Cannot help when BOTH host and seed are dead (needs an interactive
    host `claude` login); does NOT reflect the agent's live tmpfs token — only keeps the seed
    as fresh as the host.
    """
    if (
        load_env(root / ".catraz" / ".env").get("AUTH_MODE", "subscription")
        != "subscription"
    ):
        return
    if _credentials_mode(root) == "persistent":
        return  # §05.6: persistent profiles have nothing to sync from the host
    had = (claude_home(root) / ".credentials.json").exists()
    if not had:
        out.info("• subscription credential missing — attempting sync…")
    try:
        _run_sync(root, out, quiet=had)
    except CliError as e:
        if not had:
            out.warn(str(e) + " — run `catraz sync` once authenticated")
