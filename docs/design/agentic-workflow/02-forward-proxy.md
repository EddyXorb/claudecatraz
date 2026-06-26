# 02 — Forward-Proxy

Squid-basierter Allowlist-Egress für Research und Build. Implementiert.

Der Forward-Proxy ist der **zweite, vom Warden getrennte** Egress-Punkt: Über ihn darf der Agent ins Internet — aber nur zu einer kuratierten Domain-Allowlist. Er hält **keine** Credentials und ist **keine** R1–R6-Komponente; er adressiert allein **Exfiltration & Supply-Chain**.

```
claude-dev-env ──http(s)_proxy:3128──▶ forward-proxy (Squid) ──egress-net──▶ Internet
               NUR allowlistete Ziele; HTTPS per SNI-peek+splice (kein TLS-Decrypt)
```

**Restrisiko:** Allowlistete Hosts mit Schreib-/Echo-Eigenschaften bleiben theoretische Exfil-Kanäle — Liste eng halten + Logs auditieren.

---

## Entscheidungen

| # | Entscheidung |
| - | ------------ |
| Produkt | **Squid** — `dstdomain`-ACLs, SNI-peek ohne Bump, `access.log`. |
| TLS | **Kein MITM** — SNI-peek + splice: Squid filtert am SNI-Servernamen im Handshake, ohne zu entschlüsseln. Kein CA im Agenten, kein Cert-Pinning-Bruch. |
| CONNECT vs. SNI | SNI-peek, damit CONNECT-Host-Spoofing abgefangen wird. |
| Ports | Nur 80/443; CONNECT auf alles andere → deny. |
| IP-Literale | Nicht erlaubt — kein `dstdomain`-Treffer → default-deny. |
| Proxy-Auth | Keine — Netz ist die Grenze. |
| Caching | Aus (`cache deny all`) — Filter + Audit, kein Disk-State. |
| DNS | Squid löst selbst auf; Agent hat keine direkte DNS-Route → DNS-Tunneling strukturell zu. |
| GitHub | Vorerst nicht allowlistet. Bei Bedarf als „Code (read)"-Kategorie ergänzen. |

---

## Netz-Topologie

```
claude-dev-env (NUR agent-net) ──CONNECT/GET:3128──▶ forward-proxy ──egress-net──▶ Internet
```

- **`agent-net` ist `internal: true`** → kein direkter Internet- oder DNS-Pfad für den Agenten.
- Nur Forward-Proxy (und Warden) haben Zugang zu `egress-net`.
- Fail-closed: fällt der Proxy aus, verliert der Agent Research/Build-Egress — kein Direktzugriff möglich.

---

## `config/squid.conf`

```squid
http_port 3128
visible_hostname forward-proxy

acl allowed_domains dstdomain "/etc/squid/allowlist.txt"

# SNI-peek für HTTPS (kein Bump/Decrypt)
acl step1 at_step SslBump1
ssl_bump peek step1
acl allowed_sni ssl::server_name "/etc/squid/allowlist.txt"
ssl_bump splice allowed_sni
ssl_bump terminate all

acl safe_ports port 80 443
acl CONNECT method CONNECT
http_access deny CONNECT !safe_ports

http_access allow allowed_domains
http_access deny all               # DEFAULT-DENY

cache deny all

logformat audit %ts.%03tu %>a %Ss/%03>Hs %<st %rm %ru %ssl::>sni
access_log /var/log/squid/access.log audit
```

`dstdomain` und `ssl::server_name` lesen **dieselbe** `allowlist.txt` → eine Wahrheit für HTTP und HTTPS.

---

## `config/allowlist.txt`

```
# Paket-Registries
.npmjs.org
.crates.io
static.crates.io
.pypi.org
files.pythonhosted.org
.conan.io
center.conan.io
# Toolchain
apt.llvm.org
sh.rustup.rs
static.rust-lang.org
deb.nodesource.com
# Doku & Q&A
docs.gitlab.com
doc.rust-lang.org
docs.python.org
stackoverflow.com
# GitHub vorerst nicht im Scope
```

`.domain` = inkl. Subdomains. Paketmanager folgen Redirects auf CDNs → transitive Hosts müssen mit drauf.

Allowlist-Reload ohne Neustart: `docker compose exec forward-proxy squid -k reconfigure`.

---

## Deployment

```yaml
  forward-proxy:
    build: ./forward-proxy           # squid mit SSL-Support
    networks: [agent-net, egress-net]
    volumes:
      - ./config/squid.conf:/etc/squid/squid.conf:ro
      - ./config/allowlist.txt:/etc/squid/allowlist.txt:ro
      - ./logs/squid:/var/log/squid
    read_only: true
    tmpfs: [/var/spool/squid, /tmp]
    healthcheck:
      test: ["CMD", "squidclient", "-h", "127.0.0.1", "mgr:info"]
    restart: unless-stopped
```

Agent-Env:

```yaml
  claude-dev-env:
    networks: [agent-net]
    environment:
      - http_proxy=http://forward-proxy:3128
      - https_proxy=http://forward-proxy:3128
      - no_proxy=gitlab-warden       # GitLab läuft über den Warden
```

Build-Egress (cargo/npm/pip/conan) läuft über denselben Proxy. Claude Codes WebSearch läuft serverseitig über Anthropic — nicht über diesen Proxy.

---

## Logging

`access.log` im `audit`-Format: Zeitstempel, Client, Squid-Status, Methode, Ziel-URL, SNI. Rotation per `squid -k rotate`. Auswertbar als Exfil-Erkennungsnetz; fließt optional in den Observability-Stack ein (→ [`03-observability.md`](./03-observability.md)).

```bash
grep <ziel> logs/squid/access.log
```

---

## Tests

Übergreifende Red-Team-Suite → [`03-testing-redteam.md`](./03-testing-redteam.md) (Fall A11).

| Was | Erwartung |
| --- | --------- |
| `pip install`/`npm install`/`cargo fetch` über den Proxy | ✅ erfolgreich |
| `curl https://allowlisted` | ✅ splice, durchgereicht |
| `curl https://evil.example.com` | ❌ `ssl_bump terminate` |
| `curl http://evil.example.com` | ❌ `http_access deny all` |
| CONNECT zu allowlistetem Host:22 | ❌ `!safe_ports` |
| CONNECT zu IP-Literal | ❌ kein `dstdomain`-Treffer |
| Exfil-POST zu nicht-allowlistetem Host | ❌ block + im `access.log` sichtbar |

Allowlist gegen einen Clean-Build testen — fehlende CDN-Hosts führen zu stillen Build-Brüchen.
