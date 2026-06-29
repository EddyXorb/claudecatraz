#!/bin/sh
# Squid startup for the Forward-Proxy.
# Runs as root, sets up the few writable paths and starts Squid in the foreground.
# Squid itself drops to the `proxy` user (cache_effective_user, Debian default)
# -> a read-only root FS remains possible; only the bind-mounted /var/log/squid
# and the tmpfs paths are writable.
set -e

CERTGEN=/usr/lib/squid/security_file_certgen
SSL_DB=/var/spool/squid/ssl_db   # tmpfs

# Give the log directory (bind-mount) to the Squid user.
mkdir -p /var/log/squid
chown -R proxy:proxy /var/log/squid 2>/dev/null || true

# Initialise the SSL certificate database. Squid starts the ssl-bump engine with
# the certgen helper even when we only peek/splice (never bump) -> the DB must
# exist and be owned by the proxy user, otherwise the helper crashes on startup.
rm -rf "$SSL_DB"
"$CERTGEN" -c -s "$SSL_DB" -M 4MB
chown -R proxy:proxy "$SSL_DB"

# -N: no daemon fork (foreground for Docker/PID 1); -d1: logs to stderr.
exec squid -N -d1 -f /etc/squid/squid.conf
