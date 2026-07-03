"""Path-template → regex, shared by every guard's endpoint tables (F10-style
Clean-Code vorarbeiten, ``docs/design/architecture-generalization/06-migration.md``).

A template like ``/projects/{id}/merge_requests/{iid}`` compiles to a regex that
matches exactly one non-slash, URL-encoded path segment per ``{...}`` token.
Kernel-owned (§03.3: "path_template.py dahin, wo es alle erreichen") since more
than one guard's endpoint table (the REST catalog, the REST read-endpoint
table, and any future path-shaped guard) needs the same compiler — one place
instead of near-identical copies.
"""

from __future__ import annotations

import re


def compile_template(template: str) -> re.Pattern[str]:
    """Compile a ``{placeholder}`` path template into a fullmatch-ready regex."""
    segments = []
    for seg in template.split("/"):
        if seg.startswith("{") and seg.endswith("}"):
            segments.append("[^/]+")
        else:
            segments.append(re.escape(seg))
    return re.compile("/".join(segments))
