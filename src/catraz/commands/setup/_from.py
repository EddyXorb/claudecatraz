"""Helpers for `catraz init --from <path>`: load inherited values from an existing sandbox."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from catraz.envfile import load_env
from catraz.errors import CliError, EXIT_CONFIG

# Curated .env keys that are portable across hosts (no DEV_UID, no WARDEN_*).
# The git host lives in warden.toml (inherited as a config file), not .env.
_ENV_ALLOWLIST = (
    "AUTH_MODE",
    "BASE_IMAGE",
    "BASE_DOCKERFILE",
    "BASE_CONTEXT",
)

# config/ files eligible for inheritance.
_CONFIG_FILES = (
    "image/Dockerfile",
    "warden.toml",
    "squid.conf",
    "allowlist.txt",
)


def load_inherited(src_root: Path) -> dict[str, Any]:
    """Validate and load inheritable state from *src_root*: curated .env
    keys, config/ file paths, and secrets/ file paths, keyed by name.
    Raises CliError if src_root/.catraz does not exist."""
    src_cat = Path(src_root).resolve() / ".catraz"
    if not src_cat.is_dir():
        raise CliError(
            f"--from: no .catraz at {src_cat} — "
            "specify a directory that has already been initialised with catraz init",
            EXIT_CONFIG,
        )

    # Curated .env keys.
    src_env_path = src_cat / ".env"
    src_env = load_env(src_env_path) if src_env_path.exists() else {}
    inherited_env = {k: v for k, v in src_env.items() if k in _ENV_ALLOWLIST and v}

    # config/ files (only existing ones).
    inherited_config: dict[str, Path] = {}
    for name in _CONFIG_FILES:
        p = src_cat / "config" / name
        if p.exists():
            inherited_config[name] = p

    # secrets/ directory and its children (recursive, exclude claude/ sub-tree handled separately).
    inherited_secrets: dict[str, Path] = {}
    src_secrets = src_cat / "secrets"
    if src_secrets.is_dir():
        for child in src_secrets.iterdir():
            if child.is_dir():
                inherited_secrets[child.name] = child
            elif child.is_file():
                # Skip empty/whitespace-only files (placeholder stubs) — inheriting
                # them would make the wizard show "inherited (hidden)" for an unset token.
                try:
                    content = child.read_text(encoding="utf-8", errors="replace").strip()
                except OSError:
                    continue
                if content:
                    inherited_secrets[child.name] = child

    return {
        "env": inherited_env,
        "config": inherited_config,
        "secrets": inherited_secrets,
    }


def stage_inherited(
    cat: Path,
    inherited: dict[str, Any],
    *,
    yes: bool,
    out: Any,
) -> None:
    """Copy inherited config files and secrets into *cat* (.catraz directory).

    Secrets are copied WITHOUT ever printing their values.  Config files are copied
    silently; the wizard will display them as defaults in its prompts.
    """
    # config/ files
    for name, src_path in inherited.get("config", {}).items():
        dst = cat / "config" / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists() or yes:
            shutil.copy2(src_path, dst)
            out.info(f"  • inherited config/{name} from --from")

    # secrets/ — copy without echo
    for name, src_path in inherited.get("secrets", {}).items():
        dst = cat / "secrets" / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src_path.is_dir():
            # Recurse into sub-directories (e.g. claude/).
            dst.mkdir(mode=0o700, exist_ok=True)
            dst.chmod(0o700)
            for child in src_path.rglob("*"):
                rel = child.relative_to(src_path)
                d = dst / rel
                if child.is_dir():
                    d.mkdir(mode=0o700, exist_ok=True)
                    d.chmod(0o700)
                else:
                    if not d.exists() or yes:
                        d.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(child, d)
                        d.chmod(0o600)
            out.info(f"  • inherited secrets/{name}/ (contents not shown)")
        else:
            dst_empty = (
                dst.is_file() and not dst.read_text(encoding="utf-8", errors="replace").strip()
            )
            if not dst.exists() or yes or dst_empty:
                shutil.copy2(src_path, dst)
                dst.chmod(0o600)
                # Intentionally do NOT log the value.
                out.info(f"  • inherited secrets/{name} (value not shown)")
