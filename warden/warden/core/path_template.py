"""Path-template -> regex, shared by every REST guard's recognizer catalog.

{name} is one non-empty path segment, {name:int} one all-digit segment.
{rest} requires further characters after its slash; a trailing {/rest}
makes that tail optional."""

from __future__ import annotations

import re
from urllib.parse import unquote

_OPTIONAL_TAIL = "{/rest}"


def compile_template(template: str) -> re.Pattern[str]:
    """Compile a path template into a fullmatch-ready regex."""
    optional_tail = template.endswith(_OPTIONAL_TAIL)
    if optional_tail:
        template = template[: -len(_OPTIONAL_TAIL)]
    segments = []
    for seg in template.split("/"):
        if seg == "{rest}":
            segments.append(".+")
        elif seg.startswith("{") and seg.endswith(":int}"):
            segments.append("[0-9]+")
        elif seg.startswith("{") and seg.endswith("}"):
            segments.append("[^/]+")
        else:
            segments.append(re.escape(seg))
    pattern = "/".join(segments)
    if optional_tail:
        pattern += "(?:/.+)?"
    return re.compile(pattern)


_UNRESERVED_ESCAPE = re.compile(
    r"%(4[1-9A-Fa-f]|5[0-9Aa]|6[1-9A-Fa-f]|7[0-9Aa]|3[0-9]|2[DEde]|5[Ff]|7[Ee])"
)


def decode_unreserved(path: str) -> str:
    """Decode percent-escapes of unreserved characters (letters, digits,
    "-._~"), which every upstream treats as the plain character — so
    "/%63ontents" is decided, and forwarded, as "/contents"."""
    return _UNRESERVED_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), path)


def has_dot_segment(path: str) -> bool:
    """True iff any segment, raw or percent-decoded, is "." or "..".

    The upstream client normalises dot segments away, so a template match
    on such a path would be forwarded to a different endpoint than decided."""
    decoded = unquote(path)
    return any(seg in (".", "..") for seg in (*path.split("/"), *decoded.split("/")))
