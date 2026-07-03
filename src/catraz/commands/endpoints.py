"""``catraz allow-endpoint`` (§04.2/04.3, docs/design/agentic-workflow/04-cli.md):
activate an endpoint-catalog entry beyond the shipped default set.

Prefers a live, verified edit (query the running warden's ``/policy`` route
for the real catalog + current activation state, merge, write). Degrades to
an offline mode when the stack isn't running — see ``_allow_endpoint_offline``
for exactly what that can and cannot do safely.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from catraz.admin_client import AdminUnreachable
from catraz.endpoints import (
    fetch_policy_report,
    merge_endpoint_ids,
    read_enable_list,
    render_enable_block,
    validate_endpoint_id_shape,
    write_enable_list,
)
from catraz.errors import EXIT_CONFIG, EXIT_OK, CliError
from catraz.ui import Out


def cmd_allow_endpoint(root: Path, args: argparse.Namespace, out: Out) -> int:
    warden_toml = root / ".catraz" / "config" / "warden.toml"
    if not warden_toml.exists():
        raise CliError("not set up — run catraz init", EXIT_CONFIG)

    requested: list[str] = []
    for endpoint_id in args.endpoint_ids:
        reason = validate_endpoint_id_shape(endpoint_id)
        if reason:
            out.warn(f"skipping {endpoint_id!r}: {reason}")
        else:
            requested.append(endpoint_id)
    if not requested:
        out.err("nothing to enable")
        return EXIT_CONFIG

    try:
        report = fetch_policy_report(root)
    except AdminUnreachable as exc:
        return _allow_endpoint_offline(warden_toml, requested, str(exc), out)

    catalog_ids = {row["id"] for row in report["catalog"]}
    unknown = [eid for eid in requested if eid not in catalog_ids]
    if unknown:
        out.err(
            f"unknown catalog id(s): {', '.join(unknown)} — "
            "run `catraz doctor --section endpoints` to list valid ids"
        )
        return EXIT_CONFIG

    active_ids = [row["id"] for row in report["catalog"] if row["active"]]
    merged = merge_endpoint_ids(active_ids, requested)
    if merged == active_ids:
        out.info("already enabled — nothing to add")
        return EXIT_OK

    write_enable_list(warden_toml, merged)
    out.info(out.green(f"• [api.endpoints].enable now: {', '.join(merged)}"))
    out.info("  run `catraz reload` for the warden to pick this up")
    return EXIT_OK


def _allow_endpoint_offline(
    warden_toml: Path, requested: list[str], reason: str, out: Out
) -> int:
    out.warn(f"could not reach the running warden ({reason}) — catalog ids not verified")
    existing = read_enable_list(warden_toml)
    if existing is not None:
        # warden.toml already has an explicit [api.endpoints].enable — that
        # IS the current activation state, no live catalog needed to merge.
        merged = merge_endpoint_ids(existing, requested)
        if merged == existing:
            out.info("already enabled — nothing to add")
            return EXIT_OK
        write_enable_list(warden_toml, merged)
        out.info(out.green(f"• [api.endpoints].enable now: {', '.join(merged)}"))
        out.warn(
            "id(s) were not checked against the live catalog — run `catraz run` then "
            "`catraz doctor --section endpoints` to confirm"
        )
        out.info("  run `catraz reload` for the warden to pick this up")
        return EXIT_OK

    # No existing [api.endpoints] section AND no live warden to ask what the
    # current default set even is. Writing here would mean either silently
    # narrowing the active set (a bare `enable = [<requested>]` drops the
    # shipped defaults) or hardcoding the default set client-side — exactly
    # the drift risk the catalog exists to eliminate (F10). Refuse to write;
    # hand the human the exact block instead.
    out.err(
        "no [api.endpoints] section yet, and the warden isn't running to confirm the "
        "current default set"
    )
    out.info(
        "start the stack (`catraz run` / `catraz up`) and re-run this command for a "
        f"verified, automatic edit — or paste this into {warden_toml} yourself (note: "
        "this REPLACES the default-activated set; include the ids you still want, see "
        "`catraz doctor` once the stack is up):\n"
    )
    print(render_enable_block(requested))
    out.info("then run `catraz reload`")
    return EXIT_CONFIG
