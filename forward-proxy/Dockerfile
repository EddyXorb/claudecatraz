# Forward-Proxy — Squid mit OpenSSL-Support (ssl_bump peek/splice).
# Realisiert docs/design/agentic-workflow/02-forward-proxy/ (01..04).
#
# KEIN TLS-MITM: HTTPS wird per SNI-peek gefiltert und gespliced (durchgereicht),
# nie entschlüsselt. Das hier erzeugte Zertifikat dient ausschliesslich der
# ssl-bump-Initialisierung von Squid und wird beim Splicing NIE dem Client
# praesentiert -> kein CA im Agenten (README §3).
FROM debian:bookworm-slim

# Debian-Paket `squid-openssl` == Squid mit `--with-openssl` (ssl_bump verfuegbar).
# `squidclient` fuer Diagnose, `openssl` fuer das Init-Zertifikat.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      squid-openssl squidclient openssl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Squid laeuft als `proxy`. Damit das bind-gemountete Audit-Log auf dem Host ohne
# Docker-Tools lesbar ist (README §11.6), bekommt `proxy` die Host-UID des Auditors.
ARG DEV_UID=1000
RUN usermod -u ${DEV_UID} proxy && groupmod -g ${DEV_UID} proxy

# Selbst-signiertes Init-Zertifikat (nur fuer die ssl-bump-Engine, nie am Client).
RUN mkdir -p /etc/squid/ssl \
 && openssl req -x509 -newkey rsa:2048 -nodes \
      -keyout /etc/squid/ssl/bump.key \
      -out /etc/squid/ssl/bump.crt \
      -subj "/CN=forward-proxy" -days 3650 \
 && cat /etc/squid/ssl/bump.crt /etc/squid/ssl/bump.key > /etc/squid/ssl/bump.pem \
 && chmod 0400 /etc/squid/ssl/bump.key /etc/squid/ssl/bump.pem \
 && chown proxy:proxy /etc/squid/ssl/bump.key /etc/squid/ssl/bump.crt /etc/squid/ssl/bump.pem

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 3128
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
