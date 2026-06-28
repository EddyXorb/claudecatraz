import os
import subprocess
import sys
from pathlib import Path

from catraz.envfile import load_env
from catraz.errors import CliError, EXIT_GENERAL
from catraz.ui import Out


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
    from catraz.paths import asset_root, claude_home
    entry = asset_root() / "assets" / "container" / "entrypoint.py"
    if not entry.exists():
        raise CliError(
            "entrypoint.py asset not found (corrupt cache? remove ~/.cache/catraz)",
            EXIT_GENERAL,
        )
    env = load_env(root / ".catraz" / ".env")
    home = claude_home(root)
    cmd = [sys.executable, str(entry), "sync", "--claude-home", str(home)]
    src = source or os.environ.get("CLAUDE_CREDENTIAL_SOURCE") or env.get("CLAUDE_CREDENTIAL_SOURCE")
    if src:
        cmd += ["--from", str(Path(src).expanduser())]
    r = subprocess.run(cmd, cwd=root, env=dict(os.environ), capture_output=quiet, text=True)
    if r.returncode != 0:
        raise CliError("credential sync failed", EXIT_GENERAL)


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
    from catraz.paths import claude_home
    if load_env(root / ".catraz" / ".env").get("AUTH_MODE", "subscription") != "subscription":
        return
    had = (claude_home(root) / ".credentials.json").exists()
    if not had:
        out.info("• subscription credential missing — attempting sync…")
    try:
        _run_sync(root, out, quiet=had)
    except CliError as e:
        if not had:
            out.warn(str(e) + " — run `catraz sync` once authenticated")
