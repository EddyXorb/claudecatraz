"""Pure request-shape helpers for the REST guard: the pieces
ApiGuard.parse composes from.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import parse_qsl, unquote

from starlette.requests import Request

from .recognizers import Location, RestRecognizer

_PROJECT_RE = re.compile(r"/projects/([^/]+)")
_API_PREFIX = "/api/v4"


def raw_rest_path(request: Request) -> str:
    """REST path after /api/v4, keeping percent-encoding (e.g. %2F in project ids).

    ASGI servers decode scope["path"] — which would turn group%2Fproj into
    a two-segment group/proj and break id extraction and forwarding. We read
    raw_path to preserve the encoding all the way to gitlab.com. A
    /api/graphql* path has no /api/v4 prefix to strip, so it passes
    through unchanged — which is exactly what marks it as GraphQL downstream.

    Deliberately query-less: path matching/decision operate on the path alone.
    The query string is extracted separately and reattached only when forwarding.
    """
    raw = request.scope.get("raw_path")
    full = raw.decode("latin-1") if raw else request.url.path
    full = full.split("?", 1)[0]
    if full.startswith(_API_PREFIX):
        full = full[len(_API_PREFIX) :]
    return full or "/"


def raw_query(request: Request) -> str:
    """Raw query string (percent-encoding intact), for the upstream URL only.

    The *decision* reads decoded fields via request.query_params (folded
    into intent.fields by extract_fields); this preserves
    the exact wire bytes GitLab must see — without it, a query-dependent decision
    could pass on a value the upstream request never actually carries.
    """
    raw: bytes = request.scope.get("query_string", b"")
    return raw.decode("latin-1")


def project_from_path(path: str) -> str:
    m = _PROJECT_RE.search(path)
    if not m:
        return ""
    return unquote(m.group(1))


def iid_from_path(path: str) -> Optional[int]:
    m = re.search(r"/merge_requests/(\d+)", path)
    return int(m.group(1)) if m else None


def parse_body_fields(body: bytes, content_type: str) -> dict[str, Any]:
    """Parse a JSON or form-encoded body into a flat field dict — no deep schema parsing.
    Pure; a parse failure yields no fields, never an exception.
    """
    if not body:
        return {}
    try:
        if "application/json" in content_type:
            data = json.loads(body)
            if isinstance(data, dict):
                return {k: v for k, v in data.items() if isinstance(v, (str, int, bool))}
        elif "application/x-www-form-urlencoded" in content_type:
            return dict(parse_qsl(body.decode()))
    except (ValueError, UnicodeDecodeError):
        pass
    return {}


def extract_fields(
    request: Request, body: bytes, match: Optional[RestRecognizer]
) -> dict[str, Any]:
    """Pull the decision fields for this request.

    An unmatched request needs none — nothing recognizes it either way.

    For a matched recognizer: only the fields it *declares*
    (RestRecognizer.decision_fields) are read, each strictly from its
    declared location (body or query) — never a blind merge of both. A
    body-declared field that only appears in the query string (or vice versa)
    is simply absent from the decision, exactly as if the caller never sent it.
    """
    if match is None:
        return {}
    query_fields = dict(request.query_params)
    body_fields = parse_body_fields(body, request.headers.get("content-type", ""))
    fields: dict[str, Any] = {}
    for spec in match.decision_fields:
        source = query_fields if spec.location is Location.QUERY else body_fields
        if spec.name in source:
            fields[spec.name] = source[spec.name]
    return fields
