"""Endpoint-catalog CLI support (§04.2/04.3, docs/design/agentic-workflow/04-cli.md
``allow-endpoint``, ``doctor``): shape validation, [api.endpoints] TOML
read/write, and the /policy admin-route fetch.

Catalog ids and their meaning are only known to the running warden — catraz
never imports warden's Python (A2), it only ships it as a container asset
(see pyproject.toml's force-include). Offline, this module can validate an
id's *shape* only; the actual catalog is learned from the live ``/policy``
route (``admin_client.get_json``).
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any, Optional

from catraz.admin_client import get_json
from catraz.policy import set_toml_list

# mr.create, branch.create, mr.discussion_reply, … — namespace.name, lowercase,
# dot-separated. Format-only: whether the id names a *real* catalog entry is
# something only the live warden can answer (fetch_policy_report).
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


def validate_endpoint_id_shape(endpoint_id: str) -> Optional[str]:
    """Return an error reason for a malformed catalog id, or None if plausible."""
    endpoint_id = endpoint_id.strip()
    if not endpoint_id:
        return "empty"
    if not _ID_RE.match(endpoint_id):
        return "must look like 'namespace.name' (lowercase, dot-separated, e.g. 'mr.create')"
    return None


def fetch_policy_report(root: Path) -> dict[str, Any]:
    """Fetch the running warden's ``/policy`` report (the effective catalog:
    default set + activations + overrides). Raises
    :class:`catraz.admin_client.AdminUnreachable` if the stack isn't up."""
    report: dict[str, Any] = get_json(root, "/policy")
    return report


def merge_endpoint_ids(existing: list[str], additions: list[str]) -> list[str]:
    """Append *additions* to *existing*, deduping and preserving first-seen
    order — the same shape as ``catraz.policy.merge_allowed``, kept separate
    because the two lists mean different things (projects vs. catalog ids)
    and shouldn't share a name-implied contract."""
    merged = []
    for item in [*existing, *additions]:
        if item not in merged:
            merged.append(item)
    return merged


def read_enable_list(path: Path) -> Optional[list[str]]:
    """Parse ``[api.endpoints].enable`` directly out of ``warden.toml``.

    Returns ``None`` when the key is absent — mirroring the warden's own
    "absent [api.endpoints] ⇒ use the catalog's default set" rule
    (``catalog.config_parse.EndpointActivation``): the CLI must preserve the
    same absent-vs-empty distinction the warden itself enforces.
    """
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    enable = data.get("api", {}).get("endpoints", {}).get("enable")
    return list(enable) if isinstance(enable, list) else None


def render_enable_block(ids: list[str]) -> str:
    """The exact TOML block to hand-paste when a safe automatic write isn't
    possible (offline, no existing section — see ``write_enable_list``)."""
    return "[api.endpoints]\nenable = " + json.dumps(ids) + "\n"


def write_enable_list(path: Path, ids: list[str]) -> None:
    """Write ``[api.endpoints].enable``, adding the section if it is wholly
    absent from the file.

    Only handles the two shapes that are safe to edit line-by-line (the same
    discipline ``catraz.policy.set_toml_list`` already uses for
    ``allowed_projects``/``branch_prefixes`` — a targeted regex replace, never
    a full TOML re-serialisation that could reformat the rest of the file):

    * no ``[api.endpoints`` mention anywhere → append a fresh
      ``[api.endpoints]\\nenable = [...]`` block at the end of the file.
    * an ``enable = [...]`` line already exists → replace it in place
      (``set_toml_list``); this is safe regardless of which section the line
      textually sits under, because the edit never moves the line.

    Raises :class:`ValueError` for the one shape it deliberately refuses: an
    ``[api.endpoints...]`` section (e.g. a hand-written
    ``[api.endpoints.overrides."x"]``) that exists but has no ``enable`` key
    yet. Appending a new top-level ``enable = [...]`` there risks TOML
    table-redefinition errors (a dotted-header table, once opened, cannot be
    reopened with an explicit ``[api.endpoints]`` later in the file) — safer
    to ask the human to add the one line themselves.
    """
    text = path.read_text(encoding="utf-8")
    if re.search(r"^enable\s*=", text, re.M):
        set_toml_list(path, "enable", ids)
        return
    if re.search(r"^\[api\.endpoints", text, re.M):
        raise ValueError(
            "warden.toml already has an [api.endpoints...] section without an "
            "'enable' key — add `enable = [...]` under [api.endpoints] by hand, "
            "then run `catraz reload`"
        )
    new_text = text.rstrip("\n") + "\n\n" + render_enable_block(ids)
    path.write_text(new_text, encoding="utf-8")
