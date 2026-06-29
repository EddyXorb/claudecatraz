# Forward-Proxy — Squid with OpenSSL support (ssl_bump peek/splice).
# Implements docs/design/agentic-workflow/02-forward-proxy/ (01..04).
#
# NO TLS-MITM: HTTPS is filtered by SNI peek and spliced (passed through),
# never decrypted. The certificate generated here is used exclusively for
# Squid's ssl-bump initialisation and is NEVER presented to the client during
# splicing -> no CA in the agent (README §3).
FROM debian:bookworm-slim

# Debian package `squid-openssl` == Squid built with `--with-openssl` (ssl_bump available).
# `squidclient` for diagnostics, `openssl` for the init certificate.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      squid-openssl squidclient openssl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Squid runs as `proxy`. So that the bind-mounted audit log is readable on the host
# without Docker tools (README §11.6), `proxy` is assigned the auditor's host UID.
ARG DEV_UID=1000
RUN usermod -u ${DEV_UID} proxy && groupmod -g ${DEV_UID} proxy

# Self-signed init certificate (only for the ssl-bump engine, never presented to the client).
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
