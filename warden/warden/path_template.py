"""Path-template → regex, shared by the write- and read-endpoint tables (F10-style
Clean-Code vorarbeiten, ``docs/design/architecture-generalization/06-migration.md``).

A template like ``/projects/{id}/merge_requests/{iid}`` compiles to a regex that
matches exactly one non-slash, URL-encoded path segment per ``{...}`` token. Both
:class:`api_endpoints.WriteEndpoint` and :class:`read_endpoints.ReadEndpoint` used
to build this regex independently — one compiler instead of two near-identical
copies.
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
