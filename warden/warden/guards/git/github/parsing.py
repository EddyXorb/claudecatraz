"""Pure request-shape helpers for the GitHub REST guard."""

from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import unquote

from starlette.requests import Request

from ....core.path_template import decode_unreserved

_API_PREFIX = "/api/v3"
_REPO_RE = re.compile(r"^/repos/([^/]+)/([^/]+)")
_NUMBER_RE = re.compile(r"^/repos/[^/]+/[^/]+/(?:pulls|issues)/([0-9]+)(?:/|$)")
_RUN_RE = re.compile(r"^/repos/[^/]+/[^/]+/actions/runs/([0-9]+)(?:/|$)")
_JOB_RE = re.compile(r"^/repos/[^/]+/[^/]+/actions/jobs/([0-9]+)(?:/|$)")


def raw_rest_path(request: Request) -> str:
    """REST path after /api/v3, percent-encoding kept, query-less."""
    raw = request.scope.get("raw_path")
    full = raw.decode("latin-1") if raw else request.url.path
    full = full.split("?", 1)[0]
    if full.startswith(_API_PREFIX):
        full = full[len(_API_PREFIX) :]
    return decode_unreserved(full) or "/"


def raw_query(request: Request) -> str:
    raw: bytes = request.scope.get("query_string", b"")
    return raw.decode("latin-1")


def project_from_path(path: str) -> str:
    """owner/repo of a /repos/{owner}/{repo}/… path, or "" for none."""
    m = _REPO_RE.match(path)
    if not m:
        return ""
    return f"{unquote(m.group(1))}/{unquote(m.group(2))}"


def _int_from(pattern: re.Pattern[str], path: str) -> Optional[int]:
    m = pattern.match(path)
    return int(m.group(1)) if m else None


def number_from_path(path: str) -> Optional[int]:
    return _int_from(_NUMBER_RE, path)


def run_id_from_path(path: str) -> Optional[int]:
    return _int_from(_RUN_RE, path)


def job_id_from_path(path: str) -> Optional[int]:
    return _int_from(_JOB_RE, path)


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate key in JSON body")
    return dict(pairs)


def parse_json_object(body: bytes) -> Optional[dict[str, Any]]:
    """The body as one JSON object; {} when empty. None when it is anything
    else — invalid JSON, a non-object, or a duplicated key whose winner
    the upstream parser might pick differently."""
    if not body:
        return {}
    try:
        data = json.loads(body, object_pairs_hook=_reject_duplicates)
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def decision_fields(
    data: Optional[dict[str, Any]], names: tuple[str, ...]
) -> Optional[dict[str, str]]:
    """The declared decision fields of data, each a string when present.

    None when data is undecidable or a declared field has another type —
    a non-string state or ref must deny, never read as "absent"."""
    if data is None:
        return None
    fields: dict[str, str] = {}
    for name in names:
        if name not in data:
            continue
        value = data[name]
        if not isinstance(value, str):
            return None
        fields[name] = value
    return fields
