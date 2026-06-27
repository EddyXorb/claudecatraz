#!/bin/sh
# Warden entrypoint — läuft als root, fixiert Bind-Mount-Ownership, droppt zu warden.
# Dasselbe Muster wie forward-proxy/docker-entrypoint.sh (README §11.6).
set -e

# Bind-gemountete State- und Log-Verzeichnisse dem Warden-User geben, damit der
# non-root Service mit read-only Root-FS hineinschreiben kann.
chown -R warden:warden /var/lib/warden /var/log/warden /run/warden 2>/dev/null || true

exec gosu warden python -m warden "$@"
