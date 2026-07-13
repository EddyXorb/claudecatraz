"""Squid allowlist text model: block markers, coverage, and provenance.

Operates on the forward-proxy allowlist text (`# agent:<profile> begin/end`
marked blocks around plain `.domain`/`domain` entry lines) — never on
`warden.toml allowed_projects`, a separate allowlist with its own module."""

from __future__ import annotations

import re
from dataclasses import dataclass

_BEGIN_RE = re.compile(r"^# agent:(?P<profile>\S+) begin$")
_END_RE = re.compile(r"^# agent:(?P<profile>\S+) end$")


def _begin_marker(profile: str) -> str:
    return f"# agent:{profile} begin"


def _end_marker(profile: str) -> str:
    return f"# agent:{profile} end"


def _is_entry_line(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def _find_block(lines: list[str], profile: str) -> tuple[int, int] | None:
    begin = _begin_marker(profile)
    end = _end_marker(profile)
    begin_idx: int | None = None
    for i, line in enumerate(lines):
        if begin_idx is None:
            if line.strip() == begin:
                begin_idx = i
            continue
        if line.strip() == end:
            return begin_idx, i
    return None


def agent_block(text: str, profile: str) -> tuple[str, ...] | None:
    """Domains inside *profile*'s marked block, in file order, or None if the
    block does not exist (distinct from an existing, empty block)."""
    lines = text.split("\n")
    found = _find_block(lines, profile)
    if found is None:
        return None
    begin_idx, end_idx = found
    return tuple(line.strip() for line in lines[begin_idx + 1 : end_idx] if _is_entry_line(line))


def upsert_agent_block(text: str, profile: str, domains: tuple[str, ...]) -> str:
    """Replace the block's interior in place, append a new block at end-of-file
    if none exists, or remove it entirely when domains is empty. Content
    outside the markers is never rewritten."""
    lines = text.split("\n")
    found = _find_block(lines, profile)

    if found is None:
        if not domains:
            return text
        new_lines = list(lines)
        trailing_newline = bool(new_lines) and new_lines[-1] == ""
        if trailing_newline:
            new_lines.pop()
        new_lines.append("")
        new_lines.append(_begin_marker(profile))
        new_lines.extend(domains)
        new_lines.append(_end_marker(profile))
        if trailing_newline:
            new_lines.append("")
        return "\n".join(new_lines)

    begin_idx, end_idx = found
    if not domains:
        start = begin_idx
        if start > 0 and lines[start - 1].strip() == "":
            start -= 1
        return "\n".join(lines[:start] + lines[end_idx + 1 :])

    return "\n".join(lines[: begin_idx + 1] + list(domains) + lines[end_idx:])


def domain_covered(text: str, domain: str) -> bool:
    """True if *domain* is already reachable via an entry line (comments never
    count): an equal entry line, or a `.suffix` entry line that *domain* is a
    subdomain of or equal to (`.anthropic.com` covers `anthropic.com` too)."""
    needle = domain.strip().lower()
    for line in text.split("\n"):
        if not _is_entry_line(line):
            continue
        entry = line.strip().lower()
        if entry == needle:
            return True
        if entry.startswith(".") and (needle == entry[1:] or needle.endswith(entry)):
            return True
    return False


@dataclass(frozen=True)
class DomainEntry:
    """One allowlist entry line and where it came from."""

    entry: str
    provenance: str


def _entries_outside_blocks(text: str) -> frozenset[str]:
    entries = set()
    in_block = False
    for line in text.split("\n"):
        stripped = line.strip()
        if _BEGIN_RE.match(stripped):
            in_block = True
            continue
        if _END_RE.match(stripped):
            in_block = False
            continue
        if not in_block and _is_entry_line(line):
            entries.add(stripped)
    return frozenset(entries)


def classify_domains(text: str, baseline_text: str) -> tuple[DomainEntry, ...]:
    """Walk *text*: entries inside an `# agent:<profile>` block get that
    provenance; entries outside any block get "baseline" if the identical
    line appears outside any block in *baseline_text*, else "manual"."""
    baseline_entries = _entries_outside_blocks(baseline_text)
    result: list[DomainEntry] = []
    current_profile: str | None = None
    for line in text.split("\n"):
        stripped = line.strip()
        begin_match = _BEGIN_RE.match(stripped)
        if begin_match:
            current_profile = begin_match.group("profile")
            continue
        if _END_RE.match(stripped):
            current_profile = None
            continue
        if not _is_entry_line(line):
            continue
        if current_profile is not None:
            result.append(DomainEntry(stripped, f"agent:{current_profile}"))
        elif stripped in baseline_entries:
            result.append(DomainEntry(stripped, "baseline"))
        else:
            result.append(DomainEntry(stripped, "manual"))
    return tuple(result)
