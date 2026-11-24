# Known issues on windows

Open defects, grouped by root cause. Each entry names the source it lives in and
the tests that prove it.

## Windows host support

The stack itself is Linux (containers); the host-side CLI also runs on a Windows
host against Docker Desktop, and the CLI CI matrix covers `windows-latest`
alongside Linux. What follows are the platform boundaries that remain — inherent
to Windows, not defects a code change removes.

### Unix sockets do not exist in CPython on Windows

`socket.AF_UNIX` is absent, so the warden's admin socket is unreachable from a
Windows host. `hostfs.unix_socket()` turns that into an `OSError`, so every
caller treats it like a stopped warden: `catraz allow` and the doctor's endpoint
section report the warden as unreachable, and `catraz audit --web` refuses with
a pointer to the plain JSONL `catraz audit`. The live viewer needs a transport
Windows can dial.

- `src/catraz/hostfs.py`, `src/catraz/admin_client.py`,
  `src/catraz/live_config.py`, `src/catraz/commands/observe.py`
- Covered by: `tests/cli/test_hostfs.py`; the real-socket tests in
  `tests/cli/test_admin_client.py` and `tests/cli/test_audit.py` skip there

### POSIX file modes are ACL-only

`chmod` sets nothing but the read-only bit and `stat` reports `0o666`/`0o777`
whatever was written, so every `0600`/`0700` call rests on the filesystem ACL
alone: the secrets dir, the token files and the rendered `compose.resolved.yml`.
Doctor states this as a warning; it is the honest ceiling of the platform. The
tests assert the modes only on a POSIX host.

- `src/catraz/doctor.py`, `src/catraz/commands/setup/_secrets.py`,
  `src/catraz/commands/setup/_from.py`, `src/catraz/compose.py`

### Linux-container integration and POSIX-shell tests

The multi-host warden tests exec into a Linux warden image, the tee-streaming
test drives a POSIX shell, and the squid supervisor (PID 1 of the Linux proxy
container) is driven by POSIX signals; they skip where the daemon runs Windows
containers, no POSIX shell exists, or the host is not POSIX.

- Covered by: `tests/container/test_multi_host.py`,
  `tests/container/test_multi_host_actions.py`,
  `tests/cli/test_agent_logs.py::test_compose_run_tee_writes_file`,
  `tests/container/test_squid_supervise.py`

### mypy is not runnable on a Windows host

typeshed hides `os.chown`, `os.getuid`, `pwd.getpwnam` and `socket.AF_UNIX` on
`win32`, so `mypy src` reports errors against code that only ever executes on
Linux (the container entrypoint) or is skipped above. Silencing them with
`type: ignore` would surface as `unused-ignore` on Linux, so the checker and the
ruff lint stay Linux-only gates.
