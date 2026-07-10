"""Shared CLI error type + exit codes, extracted to avoid an import cycle through cli.py."""

EXIT_OK = 0
EXIT_GENERAL = 1
EXIT_CONFIG = 2
EXIT_DOCTOR = 3
EXIT_DOCKER = 4


class CliError(Exception):
    def __init__(self, msg: str, code: int = EXIT_GENERAL) -> None:
        super().__init__(msg)
        self.code = code
