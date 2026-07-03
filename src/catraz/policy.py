"""validate_project, _resolve_allowed_projects, _read_toml_allowed_projects."""

import os
import re
import urllib.parse
from pathlib import Path
from typing import cast


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


def _resolve_allowed_projects(root: Path, env: dict[str, str]) -> tuple[list[str], str]:
    """Env override wins over warden.toml (README §11 precedence)."""
    ov = os.environ.get("WARDEN_ALLOWED_PROJECTS") or env.get(
        "WARDEN_ALLOWED_PROJECTS", ""
    )
    if ov.strip():
        return [p.strip() for p in ov.split(",") if p.strip()], ".env override"
    toml = root / ".catraz" / "config" / "warden.toml"
    if not toml.exists():
        return [], "no warden.toml"
    return _read_toml_allowed_projects(toml), "warden.toml"


def _read_toml_allowed_projects(path: Path) -> list[str]:
    text = path.read_text()
    try:
        import tomllib  # py3.11+, read-only — we never write TOML

        return cast(list[str], tomllib.loads(text).get("allowed_projects", []))
    except ModuleNotFoundError:
        m = re.search(r"allowed_projects\s*=\s*\[(.*?)\]", text, re.S)
        if not m:
            return []
        return re.findall(r'"([^"]+)"', m.group(1))


def _host_of(s: str) -> str:
    """Lowercased hostname of a URL-ish string (scheme optional), ignoring port."""
    s = (s or "").strip()
    if "://" not in s:
        s = "https://" + s
    return (urllib.parse.urlsplit(s).hostname or "").lower()


def _project_from_remote_url(
    url: str, gitlab_url: str = "https://gitlab.com"
) -> str | None:
    """Derive a GitLab project path (group/sub/project) from a git remote URL,
    iff its host matches *gitlab_url*'s host. Else (or on an invalid path) None.

    Handles both the HTTPS form (``https://host/group/proj(.git)``) and the
    scp-like SSH form (``git@host:group/proj(.git)``). The SSH form has no ``//``,
    so feeding it to urllib.parse mangles it — special-case it by splitting on the
    first ``@`` then ``:``."""
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
    """Drop falsy entries from *existing*, append *additions*, dedupe preserving
    first-seen order. (The shipped default is ``[]``; the empty-string drop is
    defensive against an older ``[""]`` placeholder.)"""
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
    """Set a scalar string value for *key* in a TOML file.

    Uses a targeted regex replace that preserves the line's leading
    whitespace/alignment and trailing inline comment.  If the key is absent
    the new assignment is appended.  Quote strings with json.dumps so
    escaping is always valid TOML.
    """
    import json as _json

    text = path.read_text(encoding="utf-8")
    serialized = _json.dumps(value)
    pat = re.compile(
        rf'^(?P<pre>\s*{re.escape(key)}\s*=\s*)(?P<val>"[^"]*"|\[[^\]]*\])(?P<post>\s*(#.*)?)$',
        re.M,
    )
    if pat.search(text):
        new_text = pat.sub(
            lambda m: m.group("pre") + serialized + m.group("post"), text
        )
    else:
        new_text = text.rstrip("\n") + f"\n{key} = {serialized}\n"
    path.write_text(new_text, encoding="utf-8")


def set_toml_list(path: Path, key: str, values: list[str]) -> None:
    """Set a list of strings for *key* in a TOML file.

    Same comment-preserving strategy as set_toml_scalar.  The shipped
    allowed_projects line is ``allowed_projects = [""]`` (one empty string,
    not an empty list), so the regex ``\\[[^\\]]*\\]`` matches it.  If the
    key is absent the new assignment is appended.
    """
    import json as _json

    text = path.read_text(encoding="utf-8")
    serialized = _json.dumps(values)
    pat = re.compile(
        rf'^(?P<pre>\s*{re.escape(key)}\s*=\s*)(?P<val>"[^"]*"|\[[^\]]*\])(?P<post>\s*(#.*)?)$',
        re.M,
    )
    if pat.search(text):
        new_text = pat.sub(
            lambda m: m.group("pre") + serialized + m.group("post"), text
        )
    else:
        new_text = text.rstrip("\n") + f"\n{key} = {serialized}\n"
    path.write_text(new_text, encoding="utf-8")


def remove_toml_key(path: Path, key: str) -> None:
    """Delete a whole ``key = ...`` assignment line from a TOML file, if present.

    Used to retire a superseded key when a wizard writes its replacement — e.g.
    the legacy ``branch_prefix`` scalar once ``branch_prefixes`` is written —
    so the two can never coexist and trip the Warden's "one source of truth"
    ConfigError on next start. A no-op if the key is absent.
    """
    text = path.read_text(encoding="utf-8")
    pat = re.compile(
        rf'^\s*{re.escape(key)}\s*=\s*("[^"]*"|\[[^\]]*\])\s*(#.*)?\n?',
        re.M,
    )
    new_text = pat.sub("", text)
    if new_text != text:
        path.write_text(new_text, encoding="utf-8")
