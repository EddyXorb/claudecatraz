"""validate_project, _resolve_allowed_projects, _read_toml_allowed_projects."""
import os
import re
from pathlib import Path


def validate_project(p):
    """Return an error reason for an allowed_projects entry, or None if plausible.
    We can only catch the mechanically-detectable traps; group-vs-project ambiguity
    (group/sub looks like a project) is left to the warden reconcile."""
    p = p.strip()
    if not p:
        return "empty"
    if any(c in p for c in "*?[]"):
        return "wildcard/glob not allowed"
    if p.startswith("/") or p.endswith("/"):
        return "no leading/trailing slash"
    if "/" not in p:
        return "needs a full path (group/project), not a leaf name"
    return None


def _resolve_allowed_projects(root, env):
    """Env override wins over warden.toml (README §11 precedence)."""
    ov = os.environ.get("WARDEN_ALLOWED_PROJECTS") or env.get("WARDEN_ALLOWED_PROJECTS", "")
    if ov.strip():
        return [p.strip() for p in ov.split(",") if p.strip()], ".env override"
    toml = root / ".catraz" / "config" / "warden.toml"
    if not toml.exists():
        return [], "no warden.toml"
    return _read_toml_allowed_projects(toml), "warden.toml"


def _read_toml_allowed_projects(path):
    text = path.read_text()
    try:
        import tomllib  # py3.11+, read-only — we never write TOML
        return tomllib.loads(text).get("allowed_projects", [])
    except ModuleNotFoundError:
        m = re.search(r"allowed_projects\s*=\s*\[(.*?)\]", text, re.S)
        if not m:
            return []
        return re.findall(r'"([^"]+)"', m.group(1))
