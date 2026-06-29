#!/bin/sh
# Warden entrypoint — runs as root, fixes bind-mount ownership, drops to warden.
# Same pattern as forward-proxy/docker-entrypoint.sh (README §11.6).
set -e

# Give bind-mounted state and log directories to the warden user so that the
# non-root service can write to them with a read-only root filesystem.
chown -R warden:warden /var/lib/warden /var/log/warden /run/warden 2>/dev/null || true

exec gosu warden python -m warden "$@"
