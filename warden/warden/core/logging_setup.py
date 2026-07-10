"""Operational logging setup: one stdlib logging config for the whole process.

Separate from the audit log on purpose — this is plain operational logging
(startup warnings, reconcile failures, ...), written to stderr and to a file.
"""

from __future__ import annotations

import logging
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(log_path: str) -> None:
    """Configure the root logger: stderr + file handler, level INFO.

    Idempotent — calling this more than once (e.g. across tests) does not
    stack duplicate handlers; existing handlers are cleared first.
    """
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(_FORMAT)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(path)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
