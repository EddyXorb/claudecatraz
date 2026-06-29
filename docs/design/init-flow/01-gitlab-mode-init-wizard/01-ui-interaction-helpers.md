# Wave 01 — UI: default-aware interaction helpers

> Part of init-flow/01. Runs in wave 01 (parallel with 01-warden-gitlab-mode).

## Goal
Give the wizard one consistent way to ask a question that has a sensible default,
so the user can accept it with a single Enter (requirement 4), and so prompting is
testable instead of scattered raw `input()`/`getpass()` calls.

## Context / constraints
- `src/catraz/ui.py` defines `Out` (ANSI styling, `head/info/warn/err`). It is the
  one UI seam; `cmd_init` already receives an `out: Out`.
- Tests construct `Out(color=False)` and monkeypatch `builtins.input` /
  `getpass.getpass` (see `tests/cli/test_secrets.py`). Helpers must be monkeypatch-
  friendly and must not require a TTY in tests.
- Non-interactive runs (`--yes`, pipes, CI) must never block on input.

## Approach
Add **three** small methods to `Out` — `ask`, `choice`, `secret` (no `confirm`:
the wizard has no yes/no question; every prompt is a pick-one, free-text, or
masked secret — roast iter-2 #6). They render the prompt + `[default]`, read one
line (or a secret), and fall back to the default on empty input or EOF. Keep them
dependency-free and side-effect-only on stdin/stdout so existing monkeypatching
keeps working. (No `isatty` claim: `--yes` never calls these, so EOF/empty is the
only fallback they promise — roast iter-2 #5.)

## Steps

1. **`ask`** — free-text with default:
   ```python
   def ask(self, prompt, default=None):
       suffix = f" [{default}]" if default not in (None, "") else ""
       try:
           raw = input(f"  {prompt}{suffix}: ").strip()
       except EOFError:
           return default or ""
       return raw or (default or "")
   ```

2. **`choice`** — pick one of N labelled options with a default index. **Bounded**
   retry so constant-junk stdin (a piped/monkeypatched `input` that always yields
   non-empty garbage) cannot infinite-loop (roast iter-2 #4):
   ```python
   def choice(self, prompt, options, default=0):
       # options: list[(value, label)]; returns the chosen value.
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
   ```

3. **`secret`** — masked entry, no default echo, optional "keep existing":
   ```python
   def secret(self, prompt, *, current=""):
       import getpass
       if current:
           self.info(f"  {prompt} — already set ({mask(current)}); Enter to keep.")
       try:
           val = getpass.getpass(f"  {prompt}: ").strip()
       except EOFError:
           return current
       return val or current
   ```
   Import `mask` from `catraz.envfile` (already used in `setup.py`). If a circular
   import appears, inline a 3-line mask helper in `ui.py` instead.

## Tests
- New `tests/cli/test_ui_prompts.py`:
  - `ask` returns default on empty input and on `EOFError`; returns typed value
    otherwise (monkeypatch `builtins.input`).
  - `choice` returns the default value on empty input; returns the selected value
    for a valid number; **terminates and returns the default after 3 junk inputs**
    (feed a constant non-empty junk stub — proves the loop is bounded, roast
    iter-2 #4).
  - `secret` returns `current` when input empty; returns typed secret otherwise
    (monkeypatch `getpass.getpass`).

## Success criteria
- All three helpers are pure-ish (only stdin/stdout), never raise on EOF, and
  `choice` always terminates.
- `pytest tests/cli/test_ui_prompts.py` green.

## Revision history
- v0: initial draft
- v1 (roast iter-2): dropped unused `confirm`; bounded `choice` retry; removed the
  unimplemented non-interactive-stdin claim (#4,#5,#6).
