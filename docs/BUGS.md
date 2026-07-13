# Known bugs

Open defects, grouped by root cause. Each entry names the source it lives in and
the tests that prove it.

## Windows host support

The stack itself is Linux (containers); these are defects of the host-side CLI
running on a Windows host against Docker Desktop. The suite there fails 18 tests
plus one collection error, all from the four causes below.

### Unix sockets do not exist in CPython on Windows

`socket.AF_UNIX` is absent, so the whole admin/audit path is unreachable from a
Windows host: `admin_client.py` and `commands/observe.py` open the warden's
socket directly. This is the widest of the four — `catraz observe`, `catraz
audit` and the doctor's socket probe cannot work until the warden offers a
transport that Windows can dial.

- `src/catraz/admin_client.py:26`, `src/catraz/commands/observe.py:41`
- Proven by: `tests/cli/test_admin_client.py` (fails at import),
  `tests/cli/test_audit.py::test_audit_web_forwards_to_uds`

### POSIX file modes are inert

`chmod` sets nothing but the read-only bit and `stat` reports `0o666`/`0o777`
whatever was written, so every `0600`/`0700` call is a no-op: the secrets dir,
the token files and the rendered `compose.resolved.yml` rest on the filesystem
ACL alone. Doctor states this as a warning; it is the honest ceiling of the
platform, not something a mode call can repair.

- `src/catraz/doctor.py`, `src/catraz/commands/setup/_secrets.py`,
  `src/catraz/commands/setup/_from.py`, `src/catraz/compose.py`
- Proven by: `tests/cli/test_secrets.py` (6 tests),
  `tests/cli/test_compose_resolved.py` (2 tests),
  `tests/cli/test_init_wizard.py::TestYesNoTokens::test_grouped_files_empty`

### Text I/O relies on the platform default encoding

`write_text`/`read_text` without an explicit `encoding` pick the locale codec —
cp1252 on a Windows host. Non-ASCII then either fails to encode (the rendered
agent instructions carry emoji) or lands as cp1252 and breaks the next UTF-8
read (the shipped egress allowlist carries an em dash). The reader side is
already explicit; the writers are not, and the pairing is what breaks.

Both writers reached by these tests are latent rather than live: the entrypoint
runs in the container, where the default is UTF-8. The encoding still belongs in
the call — the default is an accident, not a guarantee.

- `src/catraz/assets/container/entrypoint.py` (`install_instructions`),
  `tests/cli/test_doctor_egress.py:20`
- Proven by: `tests/container/test_claude_md.py` (2 tests),
  `tests/cli/test_doctor_egress.py` (2 tests)

### Tests assert POSIX path separators

`str(Path)` yields backslashes, and these assertions compare against literal
forward slashes. Test-side only — the paths themselves are correct.

- Proven by: `tests/cli/test_compose.py::test_base_cmd_points_at_asset_and_project`,
  `tests/cli/test_sync_entry.py::test_run_sync_calls_adapter_in_process`,
  `tests/cli/test_sync_source.py` (2 tests)

### mypy is not runnable on a Windows host

typeshed hides `os.chown`, `os.getuid`, `pwd.getpwnam` and `socket.AF_UNIX` on
`win32`, so `mypy src` reports 9 errors against code that only ever executes on
Linux (the container entrypoint) or is already covered above. Silencing them
with `type: ignore` would surface as `unused-ignore` on Linux, so the checker
stays a Linux-only gate.
