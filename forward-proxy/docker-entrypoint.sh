#!/bin/sh
# Squid-Start fuer den Forward-Proxy.
# Laeuft als root, richtet die wenigen beschreibbaren Pfade ein und startet Squid
# im Vordergrund. Squid droppt selbst auf den `proxy`-User (cache_effective_user,
# Debian-Default) -> read-only Root-FS bleibt moeglich; beschreibbar sind nur das
# bind-gemountete /var/log/squid sowie die tmpfs-Pfade.
set -e

CERTGEN=/usr/lib/squid/security_file_certgen
SSL_DB=/var/spool/squid/ssl_db   # tmpfs

# Log-Verzeichnis (Bind-Mount) dem Squid-User geben.
mkdir -p /var/log/squid
chown -R proxy:proxy /var/log/squid 2>/dev/null || true

# SSL-Zertifikats-DB initialisieren. Squid startet die ssl-bump-Engine selbst dann
# mit dem certgen-Helper, wenn wir nur peek/splice (nie bump) -> die DB muss
# existieren und dem proxy-User gehoeren, sonst crasht der Helper beim Start.
rm -rf "$SSL_DB"
"$CERTGEN" -c -s "$SSL_DB" -M 4MB
chown -R proxy:proxy "$SSL_DB"

# -N: kein Daemon-Fork (Vordergrund fuer Docker/PID 1); -d1: Logs nach stderr.
exec squid -N -d1 -f /etc/squid/squid.conf
