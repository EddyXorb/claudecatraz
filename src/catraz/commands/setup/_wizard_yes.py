import os
from pathlib import Path
from typing import Any

from catraz.envfile import unset_env_keys
from catraz.policy import ensure_git_endpoint, normalize_host
from catraz.ui import Out

from ._secrets import _ensure_secret, _upsert_grouped_token, _write_secret_value


def _yes_host(env: dict[str, str]) -> str:
    """Resolve the git host for --yes: env var > inherited/existing .env >
    default. The host is the only git-routing input; access mode is derived
    from which tokens are present, never stored."""
    host = (os.environ.get("GITLAB_HOST") or env.get("GITLAB_HOST") or "gitlab.com").strip()
    return normalize_host(host) or "gitlab.com"


def _yes_apply_tokens(secrets_dir: Path, host: str, auth_mode: str, out: Out) -> None:
    """Scaffold the grouped token files and upsert env-provided tokens under
    *host*; provision anthropic_api_key under api_key auth."""
    _ensure_secret(secrets_dir, "read_tokens")
    _ensure_secret(secrets_dir, "write_tokens")
    read_t = os.environ.get("GITLAB_READ_TOKEN", "").strip()
    write_t = os.environ.get("GITLAB_WRITE_TOKEN", "").strip()
    if read_t:
        _upsert_grouped_token(secrets_dir, "read_tokens", host, read_t)
    if write_t:
        _upsert_grouped_token(secrets_dir, "write_tokens", host, write_t)

    if auth_mode == "api_key":
        env_val = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if env_val:
            _write_secret_value(secrets_dir, "anthropic_api_key", env_val)
        else:
            _ensure_secret(secrets_dir, "anthropic_api_key")


def _yes_clear_stale_policy_env(env_path: Path) -> None:
    unset_env_keys(env_path, ["WARDEN_ALLOWED_PROJECTS", "WARDEN_BRANCH_PREFIX"])


def _wizard_yes(
    env: dict[str, str],
    env_path: Path,
    secrets_dir: Path,
    warden_toml: Path,
    updates: dict[str, str],
    out: Out,
    inherited: dict[str, Any] | None = None,
) -> None:
    """Non-interactive wizard path for --yes.

    Priority: env vars > inherited > existing .env.
    """
    out.info("• --yes: skipping prompts")

    inh_env = inherited.get("env", {}) if inherited else {}

    auth_mode = (
        os.environ.get("AUTH_MODE")
        or inh_env.get("AUTH_MODE")
        or env.get("AUTH_MODE")
        or "subscription"
    )
    updates["AUTH_MODE"] = auth_mode

    host = _yes_host({**inh_env, **env})
    _yes_apply_tokens(secrets_dir, host, auth_mode, out)
    if warden_toml.exists():
        ensure_git_endpoint(warden_toml, host, "gitlab")
    _yes_clear_stale_policy_env(env_path)

    base_image = (
        os.environ.get("BASE_IMAGE", "").strip()
        or inh_env.get("BASE_IMAGE", "")
        or env.get("BASE_IMAGE", "")
    ).strip()
    base_dockerfile = (
        os.environ.get("BASE_DOCKERFILE", "").strip()
        or inh_env.get("BASE_DOCKERFILE", "")
        or env.get("BASE_DOCKERFILE", "")
    ).strip()
    base_context = (
        os.environ.get("BASE_CONTEXT", "").strip()
        or inh_env.get("BASE_CONTEXT", "")
        or env.get("BASE_CONTEXT", "")
    ).strip()
    if base_image:
        updates["BASE_IMAGE"] = base_image
        unset_env_keys(env_path, ["BASE_DOCKERFILE", "BASE_CONTEXT"])
    elif base_dockerfile:
        updates["BASE_DOCKERFILE"] = base_dockerfile
        unset_env_keys(env_path, ["BASE_IMAGE"])
        if base_context:
            updates["BASE_CONTEXT"] = base_context
