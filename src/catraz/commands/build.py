"""Agent image build command."""

from __future__ import annotations

import argparse
from pathlib import Path

from catraz import agent_image
from catraz.ui import Out


def cmd_build(root: Path, args: argparse.Namespace, out: Out) -> int:
    if args.background:
        return agent_image.refresh(root)
    return agent_image.rebuild(root, out, no_cache=args.no_cache)
