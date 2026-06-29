import os
from pathlib import Path

from catraz.doctor import SECRETS
from catraz.envfile import unset_env_keys
from catraz.policy import set_toml_list, set_toml_scalar, validate_project
from catraz.ui import Out

_VALID_GITLAB_MODES: tuple[str, ...] = ("off", "read-only", "read-write")


def _yes_gitlab_mode(env: dict[str, str]) -> str:
    """Determine GITLAB_MODE for --yes: explicit env wins, else infer from tokens."""
    mode = (os.environ.get("GITLAB_MODE") or env.get("GITLAB_MODE") or "").strip()
    if mode in _VALID_GITLAB_MODES:
        return mode
    read_t = os.environ.get("GITLAB_READ_TOKEN", "").strip()
    write_t = os.environ.get("GITLAB_WRITE_TOKEN", "").strip()
    if read_t and write_t:
        return "read-write"
    if read_t:
        return "read-only"
    return "off"


def _yes_apply_tokens(secrets_dir: Path, auth_mode: str, out: Out) -> None:
    for filename, _, _ in SECRETS:
        p = secrets_dir / filename
        env_val = os.environ.get(filename.upper(), "").strip()
        if env_val:
            p.write_text(env_val)
            p.chmod(0o600)
        elif not p.exists():
            p.write_text("")
            p.chmod(0o600)

    if auth_mode == "api_key":
        p = secrets_dir / "anthropic_api_key"
        env_val = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if env_val:
            p.write_text(env_val)
            p.chmod(0o600)
        elif not p.exists():
            p.write_text("")
            p.chmod(0o600)


def _yes_apply_warden_policy(
    env: dict[str, str],
    env_path: Path,
    warden_toml: Path,
    out: Out,
) -> None:
    raw_projects = (
        os.environ.get("WARDEN_ALLOWED_PROJECTS") or env.get("WARDEN_ALLOWED_PROJECTS", "")
    ).strip()
    if raw_projects and warden_toml.exists():
        projects = [p.strip() for p in raw_projects.split(",") if p.strip()]
        valid: list[str] = []
        for proj in projects:
            reason = validate_project(proj)
            if reason:
                out.warn(f"  WARDEN_ALLOWED_PROJECTS: skipping {proj!r}: {reason}")
            else:
                valid.append(proj)
        if valid:
            set_toml_list(warden_toml, "allowed_projects", valid)
            out.info(f"  • wrote {len(valid)} project(s) to warden.toml")

    raw_prefix = (
        os.environ.get("WARDEN_BRANCH_PREFIX") or env.get("WARDEN_BRANCH_PREFIX", "")
    ).strip()
    if raw_prefix and warden_toml.exists():
        set_toml_scalar(warden_toml, "branch_prefix", raw_prefix)

    unset_env_keys(env_path, ["WARDEN_ALLOWED_PROJECTS", "WARDEN_BRANCH_PREFIX"])


def _wizard_yes(
    env: dict[str, str],
    env_path: Path,
    secrets_dir: Path,
    warden_toml: Path,
    updates: dict[str, str],
    out: Out,
) -> None:
    """Non-interactive wizard path for --yes."""
    out.info("• --yes: skipping prompts")

    auth_mode = os.environ.get("AUTH_MODE") or env.get("AUTH_MODE") or "subscription"
    updates["AUTH_MODE"] = auth_mode

    mode = _yes_gitlab_mode(env)
    updates["GITLAB_MODE"] = mode

    gitlab_url = os.environ.get("GITLAB_URL", "").strip() or env.get("GITLAB_URL", "")
    if gitlab_url:
        updates["GITLAB_URL"] = gitlab_url

    _yes_apply_tokens(secrets_dir, auth_mode, out)
    _yes_apply_warden_policy(env, env_path, warden_toml, out)

    base_image = (os.environ.get("BASE_IMAGE", "").strip() or env.get("BASE_IMAGE", "")).strip()
    base_dockerfile = (os.environ.get("BASE_DOCKERFILE", "").strip() or env.get("BASE_DOCKERFILE", "")).strip()
    base_context = (os.environ.get("BASE_CONTEXT", "").strip() or env.get("BASE_CONTEXT", "")).strip()
    if base_image:
        updates["BASE_IMAGE"] = base_image
        unset_env_keys(env_path, ["BASE_DOCKERFILE", "BASE_CONTEXT"])
    elif base_dockerfile:
        updates["BASE_DOCKERFILE"] = base_dockerfile
        unset_env_keys(env_path, ["BASE_IMAGE"])
        if base_context:
            updates["BASE_CONTEXT"] = base_context
