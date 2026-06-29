"""Shared CLI error type + exit codes (see 04-cli.md §4).

Extracted from cli.py so paths.py/compose.py/doctor.py can import these
without an import cycle through cli.py.
"""

EXIT_OK = 0
EXIT_GENERAL = 1
EXIT_CONFIG = 2
EXIT_DOCTOR = 3
EXIT_DOCKER = 4


class CliError(Exception):
    def __init__(self, msg: str, code: int = EXIT_GENERAL) -> None:
        super().__init__(msg)
        self.code = code
