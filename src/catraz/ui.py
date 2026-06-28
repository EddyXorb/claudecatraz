"""ANSI styling for CLI output."""
import os
import sys

from catraz.envfile import mask


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

    def ask(self, prompt, default=None):
        """Free-text prompt with an optional default accepted on empty input or EOF."""
        suffix = f" [{default}]" if default not in (None, "") else ""
        try:
            raw = input(f"  {prompt}{suffix}: ").strip()
        except EOFError:
            return default or ""
        return raw or (default or "")

    def choice(self, prompt, options, default=0):
        """Pick one of N labelled options; bounded to 3 tries then falls back to default.

        options: list[(value, label)]; returns the chosen value.
        """
        self.info(prompt)
        for i, (_v, label) in enumerate(options):
            mark = "*" if i == default else " "
            self.info(f"   {mark} {i+1}) {label}")
        for _ in range(3):                       # bounded: 3 tries, then take default
            raw = self.ask(f"choose 1-{len(options)}", str(default + 1))
            try:
                idx = int(raw) - 1
            except ValueError:
                idx = -1
            if 0 <= idx < len(options):
                return options[idx][0]
            self.warn(f"enter a number 1-{len(options)}")
        return options[default][0]               # give up gracefully on junk/EOF

    def secret(self, prompt, *, current=""):
        """Masked secret entry; keeps existing value on empty input or EOF."""
        import getpass
        if current:
            self.info(f"  {prompt} — already set ({mask(current)}); Enter to keep.")
        try:
            val = getpass.getpass(f"  {prompt}: ").strip()
        except EOFError:
            return current
        return val or current
