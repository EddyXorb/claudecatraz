# 02-Forward-Proxy · 03 — Squid-Konfiguration & Allowlist

Teil von [`../02-forward-proxy.md`](../02-forward-proxy.md). Der eigentliche Filter:
deklarative `squid.conf` (default-deny, SNI-peek) und die datengetriebene `allowlist.txt`.
Baut auf der TLS-Entscheidung ([`01-scope-and-decisions.md`](./01-scope-and-decisions.md))
und der Netz-Isolation ([`02-network-isolation.md`](./02-network-isolation.md)) auf.

**Parallelität:** Präfix `03` gemeinsam mit [`03-deployment.md`](./03-deployment.md) — das
Verdrahten in `docker-compose`/Agent-Env ist unabhängig vom Authoring dieser Dateien und
kann gleichzeitig erfolgen.

Querverweise: „§x" → [`../README.md`](../README.md).

---

> **Ablageort:** Beide Dateien dieses Teils — `squid.conf` und `allowlist.txt` — liegen im
> zentralen, **host-editierbaren** `config/`-Ordner (README §11) und werden **read-only**
> in den Container gemountet ([`03-deployment.md`](./03-deployment.md)). **Keine Secrets**
> in `config/`.

## 1. `config/squid.conf` (Skizze)

`config/squid.conf` — deklarativ, default-deny:

```squid
# --- Lauschen, nur intern ---
http_port 3128
visible_hostname forward-proxy

# --- Allowlist aus Datei (eine Domain je Zeile, .domain = inkl. Subdomains) ---
acl allowed_domains dstdomain "/etc/squid/allowlist.txt"

# --- SNI-peek für HTTPS (kein Bump/Decrypt) ---
acl step1 at_step SslBump1
ssl_bump peek step1
acl allowed_sni ssl::server_name "/etc/squid/allowlist.txt"
ssl_bump splice allowed_sni        # erlaubter SNI → durchreichen, NICHT entschlüsseln
ssl_bump terminate all             # alles andere: Verbindung beenden

# --- Ports einschränken: nur 80/443 ---
acl safe_ports port 80 443
acl CONNECT method CONNECT
http_access deny CONNECT !safe_ports

# --- Plain-HTTP: nur erlaubte Hosts ---
http_access allow allowed_domains
http_access deny all               # DEFAULT-DENY

# --- Kein Caching (Cache-Poisoning/Disk vermeiden), nur Filter + Audit ---
cache deny all

# --- Audit-Log: jede Verbindung, maschinenlesbar ---
logformat audit %ts.%03tu %>a %Ss/%03>Hs %<st %rm %ru %ssl::>sni
access_log /var/log/squid/access.log audit
```

- **`dstdomain "…allowlist.txt"`** und **`ssl::server_name "…allowlist.txt"`** lesen
  **dieselbe** Datei → eine Wahrheit für HTTP und HTTPS.
- **`.domain`-Notation** in der Allowlist deckt Subdomains ab (`.npmjs.org` ⇒ auch
  `registry.npmjs.org`); exakte Hosts ohne führenden Punkt.
- **`cache deny all`:** der Proxy ist Filter + Audit, kein Cache — vermeidet
  Cache-Poisoning und Disk-State.

---

## 2. Allowlist (`config/allowlist.txt`)

Start-Set aus §6.6, projektabhängig tunen. **Wichtig:** Paketmanager folgen oft
Redirects auf CDNs → die **transitiven** Hosts müssen mit drauf, sonst bricht der Build:

```
# Paket-Registries
.npmjs.org
registry.npmjs.org
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
# Hinweis: GitHub (github.com & Co.) ist vorerst NICHT im Scope und daher nicht
# allowlistet. Bei Bedarf später als eigene "Code (read)"-Kategorie ergänzen.
```

**Pflege als Daten, nicht Code** (analog Warden W6.1): Allowlist-Edit = Review eines
Datei-Diffs, kein Squid-Logikumbau. Reload ohne Neustart via `squid -k reconfigure`.

---

## 3. Definition of Done

- [ ] `squid.conf` deklarativ, default-deny, SNI-peek/splice, `cache deny all`,
      `audit`-Logformat.
- [ ] `allowlist.txt` mit Start-Set + transitiven CDN-Hosts.
- [ ] Clean-Build aller relevanten Paketmanager läuft gegen die Allowlist (fehlende
      CDN-Hosts ergänzt) — siehe [`04-testing.md`](./04-testing.md).
- [ ] `squid -k reconfigure` lädt Allowlist-Änderungen ohne Neustart.
