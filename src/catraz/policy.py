"""validate_project, _resolve_allowed_projects, set_endpoint_allowed_projects,
set_git_rules_branch_prefixes."""

import re
import urllib.parse
from pathlib import Path


def validate_project(p: str) -> str | None:
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


def _resolve_allowed_projects(root: Path, host: str) -> tuple[list[str], str]:
    """Resolve host's allowed_projects; the single source is warden.toml."""
    toml = root / ".catraz" / "config" / "warden.toml"
    if not toml.exists():
        return [], "no warden.toml"
    return _read_toml_allowed_projects(toml, host), "warden.toml"


def _read_toml_allowed_projects(path: Path, host: str) -> list[str]:
    """allowed_projects on the [[git.endpoint]] matching host, or [] if no
    such endpoint exists or the key is absent."""
    import tomllib

    try:
        git = tomllib.loads(path.read_text(encoding="utf-8")).get("git", {})
    except (tomllib.TOMLDecodeError, OSError):
        return []
    endpoints = git.get("endpoint", []) if isinstance(git, dict) else []
    target = normalize_host(host)
    for endpoint in endpoints if isinstance(endpoints, list) else []:
        if isinstance(endpoint, dict) and normalize_host(str(endpoint.get("host", ""))) == target:
            projects = endpoint.get("allowed_projects", [])
            if isinstance(projects, list):
                return [p for p in projects if isinstance(p, str)]
            return []
    return []


def first_endpoint_host(path: Path) -> str | None:
    """Host of the first configured [[git.endpoint]], or None if none exists."""
    import tomllib

    try:
        git = tomllib.loads(path.read_text(encoding="utf-8")).get("git", {})
    except (tomllib.TOMLDecodeError, OSError):
        return None
    endpoints = git.get("endpoint", []) if isinstance(git, dict) else []
    for endpoint in endpoints if isinstance(endpoints, list) else []:
        host = endpoint.get("host") if isinstance(endpoint, dict) else None
        if isinstance(host, str) and host.strip():
            return host.strip()
    return None


def _host_of(s: str) -> str:
    """Lowercased hostname of a URL-ish string (scheme optional), ignoring port."""
    s = (s or "").strip()
    if "://" not in s:
        s = "https://" + s
    return (urllib.parse.urlsplit(s).hostname or "").lower()


def _project_from_remote_url(url: str, gitlab_url: str = "https://gitlab.com") -> str | None:
    """Derive a GitLab project path (group/sub/project) from a git remote URL
    matching gitlab_url's host, else None. Handles both HTTPS and the scp-like
    SSH form (no double slash, so urllib.parse alone can't parse it)."""
    url = (url or "").strip()
    if not url:
        return None
    target_host = _host_of(gitlab_url or "https://gitlab.com")

    if "://" in url:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
        path = parts.path
    elif "@" in url and ":" in url.split("@", 1)[1]:
        host, path = url.split("@", 1)[1].split(":", 1)
        host = host.lower()
    else:
        return None

    if host != target_host:
        return None
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    path = path.strip("/")
    if not path or validate_project(path):
        return None
    return path


def merge_allowed(existing: list[str], additions: list[str]) -> list[str]:
    """Drop falsy entries from existing, append additions, dedupe preserving
    first-seen order (guards against an older [""] placeholder default)."""
    merged = []
    for item in [*(e for e in existing if e), *additions]:
        if item not in merged:
            merged.append(item)
    return merged


def _discover_gitlab_projects(root: Path, gitlab_url: str) -> list[str]:
    """Scan *root* and its immediate git subdirs for remotes whose host matches
    *gitlab_url*; return the derived project paths (deduped, order-preserving).

    *root* is always scanned; only the immediate-subdir sweep is capped (so a huge
    folder can't stall init). One level deep — deeper trees are out of scope."""
    import subprocess

    candidates = [root]
    try:
        subdirs = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        subdirs = []
    for d in subdirs[:50]:
        if (d / ".git").exists():
            candidates.append(d)
    found: list[str] = []
    for d in candidates:
        try:
            r = subprocess.run(
                ["git", "-C", str(d), "remote", "-v"], capture_output=True, text=True
            )
        except FileNotFoundError:
            return found  # no git binary — nothing to discover
        if r.returncode != 0:
            continue
        for line in r.stdout.splitlines():
            cols = line.split()
            if len(cols) < 2:
                continue
            proj = _project_from_remote_url(cols[1], gitlab_url)
            if proj and proj not in found:
                found.append(proj)
    return found


def set_toml_scalar(path: Path, key: str, value: str) -> None:
    """Set a scalar string value for key in a TOML file, preserving formatting and
    inline comments; appends the assignment if key is absent."""
    import json as _json

    text = path.read_text(encoding="utf-8")
    serialized = _json.dumps(value)
    pat = re.compile(
        rf'^(?P<pre>\s*{re.escape(key)}\s*=\s*)(?P<val>"[^"]*"|\[[^\]]*\])(?P<post>\s*(#.*)?)$',
        re.M,
    )
    if pat.search(text):
        new_text = pat.sub(lambda m: m.group("pre") + serialized + m.group("post"), text)
    else:
        new_text = text.rstrip("\n") + f"\n{key} = {serialized}\n"
    path.write_text(new_text, encoding="utf-8")


def set_toml_list(path: Path, key: str, values: list[str]) -> None:
    """Set a list of strings for key in a TOML file; same comment-preserving
    strategy as set_toml_scalar. Matches the shipped allowed_projects = [""] line too."""
    import json as _json

    text = path.read_text(encoding="utf-8")
    serialized = _json.dumps(values)
    pat = re.compile(
        rf'^(?P<pre>\s*{re.escape(key)}\s*=\s*)(?P<val>"[^"]*"|\[[^\]]*\])(?P<post>\s*(#.*)?)$',
        re.M,
    )
    if pat.search(text):
        new_text = pat.sub(lambda m: m.group("pre") + serialized + m.group("post"), text)
    else:
        new_text = text.rstrip("\n") + f"\n{key} = {serialized}\n"
    path.write_text(new_text, encoding="utf-8")


def remove_toml_key(path: Path, key: str) -> None:
    """Delete a whole key = ... assignment line from a TOML file, if present, so
    two forms of a setting can't coexist and trip a ConfigError."""
    text = path.read_text(encoding="utf-8")
    pat = re.compile(
        rf'^\s*{re.escape(key)}\s*=\s*("[^"]*"|\[[^\]]*\])\s*(#.*)?\n?',
        re.M,
    )
    new_text = pat.sub("", text)
    if new_text != text:
        path.write_text(new_text, encoding="utf-8")


def normalize_host(host: str) -> str:
    """Case/port/trailing-dot-insensitive host key, matching the warden's rule.
    A raw host from config or a token line maps to the same key everywhere."""
    return host.split(":", 1)[0].strip().lower().rstrip(".")


def ensure_git_endpoint(path: Path, host: str, endpoint_type: str = "gitlab") -> None:
    """Append a `[[git.endpoint]]` for *host* to warden.toml unless one already
    exists (matched on normalised host). Hand-edited endpoint blocks are left
    untouched — this only fills the single-host common case."""
    import tomllib

    host = host.strip()
    if not host:
        return
    text = path.read_text(encoding="utf-8")
    try:
        git = tomllib.loads(text).get("git", {})
    except tomllib.TOMLDecodeError:
        git = {}
    endpoints = git.get("endpoint", []) if isinstance(git, dict) else []
    target = normalize_host(host)
    for endpoint in endpoints if isinstance(endpoints, list) else []:
        if isinstance(endpoint, dict) and normalize_host(str(endpoint.get("host", ""))) == target:
            return
    block = f'\n[[git.endpoint]]\nhost = "{host}"\ntype = "{endpoint_type}"\n'
    path.write_text(text.rstrip("\n") + "\n" + block, encoding="utf-8")


_ENDPOINT_HEADER_RE = re.compile(r"^\[\[git\.endpoint\]\]\s*$", re.M)


def set_endpoint_allowed_projects(path: Path, host: str, values: list[str]) -> None:
    """Set `allowed_projects` inside the `[[git.endpoint]]` table for *host*,
    touching only that block and preserving everything else. The endpoint
    must already exist (see ensure_git_endpoint)."""
    import json as _json

    text = path.read_text(encoding="utf-8")
    target = normalize_host(host)
    serialized = _json.dumps(values)
    key_pat = re.compile(
        r"^(?P<pre>\s*allowed_projects\s*=\s*)(?P<val>\[[^\]]*\])(?P<post>\s*(#.*)?)$", re.M
    )
    headers = list(_ENDPOINT_HEADER_RE.finditer(text))
    for i, m in enumerate(headers):
        block_start = m.end()
        block_end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        block = text[block_start:block_end]
        host_m = re.search(r'^\s*host\s*=\s*"([^"]*)"', block, re.M)
        if not host_m or normalize_host(host_m.group(1)) != target:
            continue
        if key_pat.search(block):
            new_block = key_pat.sub(
                lambda mm: mm.group("pre") + serialized + mm.group("post"), block
            )
        else:
            new_block = block.rstrip("\n") + f"\nallowed_projects = {serialized}\n"
        path.write_text(text[:block_start] + new_block + text[block_end:], encoding="utf-8")
        return
    raise ValueError(f"no [[git.endpoint]] for host {host!r} in {path}")


_GIT_RULES_HEADER_RE = re.compile(r"^\[git\.rules\]\s*$", re.M)


def set_git_rules_branch_prefixes(path: Path, values: list[str]) -> None:
    """Set `branch_prefixes` inside `[git.rules]`, appending the table if
    absent; preserves everything else in the file."""
    import json as _json

    text = path.read_text(encoding="utf-8")
    serialized = _json.dumps(values)
    key_pat = re.compile(
        r"^(?P<pre>\s*branch_prefixes\s*=\s*)(?P<val>\[[^\]]*\])(?P<post>\s*(#.*)?)$", re.M
    )
    m = _GIT_RULES_HEADER_RE.search(text)
    if not m:
        new_text = text.rstrip("\n") + f"\n\n[git.rules]\nbranch_prefixes = {serialized}\n"
        path.write_text(new_text, encoding="utf-8")
        return
    block_start = m.end()
    rest = text[block_start:]
    next_header = re.search(r"^\[", rest, re.M)
    block_end = block_start + next_header.start() if next_header else len(text)
    block = text[block_start:block_end]
    if key_pat.search(block):
        new_block = key_pat.sub(lambda mm: mm.group("pre") + serialized + mm.group("post"), block)
    else:
        new_block = block.rstrip("\n") + f"\nbranch_prefixes = {serialized}\n"
    path.write_text(text[:block_start] + new_block + text[block_end:], encoding="utf-8")
