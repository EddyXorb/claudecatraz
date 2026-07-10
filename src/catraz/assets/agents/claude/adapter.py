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


def _read_json(p: Path) -> dict[str, Any]:
    try:
        return cast(dict[str, Any], json.loads(p.read_text()))
    except Exception:
        return {}


def _log_dir() -> Path:
    """Where to place `--debug-file` output — the entrypoint resolves a
    durable-if-possible directory via `AGENT_LOG_DIR`; fall back to the live
    home."""
    d = os.environ.get("AGENT_LOG_DIR")
    return Path(d) if d else Path.home() / ".claude"


# ── prepare_home ─────────────────────────────────────────────────────────────


def _seed_from_ro(home: Path, ro_dir: Path | None) -> dict[str, Any]:
    """`credentials.mode = "sync"` (or first-ever start before a persistent
    login): copy the read-only seed the host synced in. Returns the
    `.claude.json` seed data (org info etc.), or defaults if none mounted."""
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


def _wire_persistent(home: Path, state_dir: Path) -> None:
    """`credentials.mode = "persistent"`: symlinks only the credential file
    and session/project state into the writable per-repo state dir; settings
    and hooks are rebuilt fresh every start so a compromised session can't
    persist. A first-ever login has nothing to symlink to yet — `claude
    login` creates the target through the dangling symlink on first write."""
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    state_dir.chmod(0o700)
    cred_link = home / ".credentials.json"
    if not cred_link.is_symlink():
        cred_link.unlink(missing_ok=True)
        cred_link.symlink_to(state_dir / ".credentials.json")
    projects_dir = state_dir / "projects"
    projects_dir.mkdir(mode=0o700, exist_ok=True)
    projects_link = home / "projects"
    if not projects_link.is_symlink():
        if projects_link.exists():
            import shutil

            shutil.rmtree(projects_link)
        projects_link.symlink_to(projects_dir)


def prepare_home(home: Path, secrets: Secrets) -> None:
    """Write credential files and settings layout into the live (tmpfs)
    home. Never touches Forge or foreign-model credentials."""
    home.mkdir(parents=True, exist_ok=True)
    manifest = _manifest()
    seed: dict[str, Any] = {}
    if secrets.auth_mode == "subscription":
        if manifest.credentials_mode == "persistent" and secrets.persistent_state_dir:
            _wire_persistent(home, secrets.persistent_state_dir)
        else:
            seed = _seed_from_ro(home, secrets.subscription_ro_dir)

    data: dict[str, Any] = seed or {
        "hasCompletedOnboarding": True,
        "lastOnboardingVersion": "1.0",
    }
    # bypassPermissionsModeAccepted moved into settings.json's
    # skipDangerousModePermissionPrompt on newer CLI versions; set both keys.
    data["bypassPermissionsModeAccepted"] = True
    if secrets.remote:
        data["remoteDialogSeen"] = True
    data.setdefault("projects", {}).setdefault("/workspace", {})["hasTrustDialogAccepted"] = True
    (Path.home() / ".claude.json").write_text(json.dumps(data, indent=2))
    (home / "settings.json").write_text(
        json.dumps(
            {
                "theme": "dark",
                "hasCompletedOnboarding": True,
                "skipDangerousModePermissionPrompt": True,
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
    return Path.home() / ".claude" / "CLAUDE.md", content


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
        sys.exit(f"error: {cred} not found — authenticate with `claude` on the host first")
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
