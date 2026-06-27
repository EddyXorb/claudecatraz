"""ANSI styling for CLI output."""
import os
import sys


class Out:
    """ANSI styling that quietly disables itself for non-TTYs / --no-color."""

    def __init__(self, color=True):
        self.color = color and sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

    def _c(self, code, s):
        return f"\033[{code}m{s}\033[0m" if self.color else s

    def bold(self, s): return self._c("1", s)
    def dim(self, s): return self._c("2", s)
    def green(self, s): return self._c("32", s)
    def yellow(self, s): return self._c("33", s)
    def red(self, s): return self._c("31", s)
    def cyan(self, s): return self._c("36", s)

    def head(self, s): print(self.bold(s))
    def info(self, s): print(s)
    def warn(self, s): print(self.yellow(f"warning: {s}"), file=sys.stderr)
    def err(self, s): print(self.red(f"error: {s}"), file=sys.stderr)
