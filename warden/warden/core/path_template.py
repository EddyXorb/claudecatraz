"""Path-template → regex, shared by every guard's endpoint tables.

A template like /projects/{id}/merge_requests/{iid} compiles to a regex
matching exactly one non-slash, URL-encoded path segment per {...} token.
Kernel-owned since multiple guards' endpoint tables need the same compiler.
"""

from __future__ import annotations

import re


def compile_template(template: str) -> re.Pattern[str]:
    """Compile a {placeholder} path template into a fullmatch-ready regex."""
    segments = []
    for seg in template.split("/"):
        if seg.startswith("{") and seg.endswith("}"):
            segments.append("[^/]+")
        else:
            segments.append(re.escape(seg))
    return re.compile("/".join(segments))
