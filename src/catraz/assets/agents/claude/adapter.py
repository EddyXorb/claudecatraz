"""Claude Code adapter: the one place that knows Claude's credential layout,
CLI flags, and remote-control support. Implements the `AgentAdapter` contract
from `agent_contract.py` as plain module-level functions, selected via the
static registry in `catraz.agents` and baked into exactly one image per
build."""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_contract import InstructionContext, Secrets, read_toml  # noqa: E402

_MANIFEST_PATH = Path(__file__).resolve().parent / "agent.toml"
_TEMPLATE_PATH = Path(__file__).resolve().parent / "AGENT.md.tmpl"


class _Manifest:
    """Typed view of this adapter's own `agent.toml`."""

    def __init__(self, data: dict[str, Any]) -> None:
        creds = data.get("credentials", {})
        self.command: str = data["command"]
        self.credentials_mode: str = creds.get("mode", "sync")
        self.remote_allowed: bool = bool(data.get("modes", {}).get("remote", False))
        self.debug_flag: str = data.get("logs", {}).get("debug_flag", "--debug-file")


def _manifest() -> _Manifest:
    return _Manifest(read_toml(_MANIFEST_PATH))


def _effective_credentials_mode(manifest: _Manifest) -> str:
    """`CLAUDE_CREDENTIALS_MODE` (set by the host from `.catraz/.env`) when it
    is `persistent`/`sync`, else the manifest default — kept in lockstep with
    the compose overlay the host picked for this same value."""
    override = os.environ.get("CLAUDE_CREDENTIALS_MODE", "").strip()
    if override in ("persistent", "sync"):
        return override
    return manifest.credentials_mode


def _read_json(p: Path) -> dict[str, Any]:
    try:
        return cast(dict[str, Any], json.loads(p.read_text()))
    except Exception:
        return {}


def _config_dir() -> Path:
    """Claude's config dir. With a custom `CLAUDE_CONFIG_DIR` (our persistent
    layout) both `.claude.json` and the user-memory `CLAUDE.md` live INSIDE it;
    the default layout keeps them under `~/.claude`. This is the single source
    of truth for every home-relative path the adapter writes, so a change to
    the compose `CLAUDE_CONFIG_DIR` never needs a matching edit here."""
    d = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(d) if d else Path.home() / ".claude"


def _log_dir() -> Path:
    """Where to place `--debug-file` output — the entrypoint resolves a
    durable-if-possible directory via `AGENT_LOG_DIR`; fall back to the config
    dir (the live home)."""
    d = os.environ.get("AGENT_LOG_DIR")
    return Path(d) if d else _config_dir()


# ── prepare_home ─────────────────────────────────────────────────────────────


def _seed_from_ro(home: Path, ro_dir: Path | None) -> dict[str, Any]:
    """`credentials.mode = "sync"`: copy the read-only credential the host
    synced in. Returns the `.claude.json` seed data (org info etc.), or
    defaults if none mounted."""
    ro = ro_dir or (home / ".ro")
    src = ro / ".credentials.json"
    if not src.exists():
        sys.exit(
            "error: subscription mode but no .credentials.json mounted "
            "(run `catraz sync`, or switch this profile to credentials.mode=persistent "
            "and `claude login` inside the container)"
        )
    import shutil

    shutil.copy2(src, home / ".credentials.json")
    if (ro / ".claude.json").exists():
        return _read_json(ro / ".claude.json")
    return {}


def prepare_home(home: Path, secrets: Secrets) -> None:
    """Write the credential/settings layout into the live home. In persistent
    mode the home is the durable bind, so seed each file only when absent and
    merge flags into an existing `.claude.json` — never clobber a login. Never
    touches Forge or foreign-model credentials."""
    home.mkdir(parents=True, exist_ok=True)
    persistent = _effective_credentials_mode(_manifest()) == "persistent"

    seed: dict[str, Any] = {}
    if secrets.auth_mode == "subscription" and not persistent:
        seed = _seed_from_ro(home, secrets.subscription_ro_dir)

    claude_json = home / ".claude.json"
    # Persistent: merge onto the existing file so the durable home is not
    # clobbered each start; otherwise (re)seed fresh.
    if persistent and claude_json.exists():
        data = _read_json(claude_json)
    else:
        data = seed or {"hasCompletedOnboarding": True, "lastOnboardingVersion": "1.0"}
    data.setdefault("hasCompletedOnboarding", True)
    data.setdefault("lastOnboardingVersion", "1.0")
    #skipDangerousModePermissionPrompt on newer CLI versions; set both keys.
    data["bypassPermissionsModeAccepted"] = True
    data["remoteDialogSeen"] = True

    data.setdefault("projects", {}).setdefault("/workspace", {})[
        "hasTrustDialogAccepted"
    ] = True

    claude_json.write_text(json.dumps(data, indent=2))

    settings = home / "settings.json"
    # Persistent: keep a settings.json the user (or a prior start) already wrote.
    if not (persistent and settings.exists()):
        settings.write_text(
            json.dumps(
                {
                    "theme": "dark",
                    "hasCompletedOnboarding": True,
                    "skipDangerousModePermissionPrompt": True,
                    "remoteDialogSeen": True,
                },
                indent=2,
            )
        )


# ── command / environ / remote_command ──────────────────────────────────────


def command(argv: list[str]) -> list[str]:
    """Argv for a one-off run."""
    m = _manifest()
    base = [m.command, "--dangerously-skip-permissions"]
    if not any(a == "-d" or a.startswith("--debug") for a in argv):
        base += [m.debug_flag, str(_log_dir() / "run-debug.log")]
    return [*base, *argv]


def environ(secrets: Secrets) -> dict[str, str]:
    """Extra env vars the agent process needs; raises on a missing api_key
    so the entrypoint fails closed."""
    if secrets.auth_mode != "api_key":
        return {}
    key = ""
    if secrets.api_key_file is not None:
        try:
            key = secrets.api_key_file.read_text(encoding="utf-8").strip()
        except OSError:
            key = ""
    key = key or secrets.api_key_env_fallback
    if not key:
        raise ValueError("api_key mode but ANTHROPIC_API_KEY unset")
    return {"ANTHROPIC_API_KEY": key}


def remote_command() -> list[str] | None:
    """Argv for the remote-control daemon, or None if this profile disables
    it (`modes.remote = false`); callers must fail closed."""
    m = _manifest()
    if not m.remote_allowed:
        return None
    spawn = os.environ.get("CLAUDE_RC_SPAWN") or "same-dir"
    debug = os.environ.get("CLAUDE_RC_DEBUG_FILE") or str(_log_dir() / "rc-debug.log")
    extra = shlex.split(os.environ.get("CLAUDE_RC_EXTRA_ARGS") or "")
    return [
        m.command,
        "remote-control",
        "--permission-mode",
        "bypassPermissions",  # keep-fixed (headless)
        "--spawn",
        spawn,
        m.debug_flag,
        debug,
        *extra,
    ]


# ── render_instructions ──────────────────────────────────────────────────────


def render_instructions(ctx: InstructionContext) -> tuple[Path, str]:
    """Render this project's namespace prefix and Warden REST base into the
    packaged template — both target and content, not a static file placed
    unchanged."""
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    prefix_example = ctx.branch_prefixes[0] if ctx.branch_prefixes else "claude/"
    content = (
        template.replace("__FORGE_REST_BASE__", ctx.forge_rest_base)
        .replace("__BRANCH_PREFIX_EXAMPLE__", prefix_example)
        .replace("__WARDEN_TOML_PATH__", str(ctx.warden_toml_path))
    )
    return _config_dir() / "CLAUDE.md", content


# ── host-side credential sync (credentials.mode = "sync") ───────────────────


def sync_from_host(source: Path | None, home: Path) -> None:
    """Copy `.credentials.json` (+ `.claude.json`) from the host `~/.claude`
    into `home` — the `catraz sync` path for `credentials.mode = "sync"`.
    Optional: adapters that only support `persistent` mode may omit it."""
    src_dir = (
        source or Path(os.environ.get("CLAUDE_CREDENTIAL_SOURCE") or "~/.claude")
    ).expanduser()
    cred = src_dir / ".credentials.json"
    if not cred.exists():
        sys.exit(
            f"error: {cred} not found — authenticate with `claude` on the host first"
        )
    import shutil

    home.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cred, home / ".credentials.json")
    # A custom config dir (e.g. ~/.claude2) keeps .claude.json INSIDE it; the default
    # ~/.claude layout keeps it as a sibling at ~/.claude.json. Prefer in-dir, then sibling.
    host_cj = src_dir / ".claude.json"
    if not host_cj.exists():
        host_cj = src_dir.parent / ".claude.json"
    dst_cj = home / ".claude.json"
    if host_cj.exists():
        shutil.copy2(host_cj, dst_cj)
    elif not dst_cj.exists():
        dst_cj.write_text(
            json.dumps(
                {"hasCompletedOnboarding": True, "lastOnboardingVersion": "1.0"},
                indent=2,
            )
        )
    print(f"Credentials synced into {home}")
