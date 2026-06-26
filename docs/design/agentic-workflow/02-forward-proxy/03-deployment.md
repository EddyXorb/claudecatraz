# 02-Forward-Proxy · 03 — Deployment & Agent-Anbindung

Teil von [`../02-forward-proxy.md`](../02-forward-proxy.md). Verdrahtet den Squid-Container
in `docker-compose` und setzt die Proxy-Variablen im Agenten. Setzt die Netz-Isolation
([`02-network-isolation.md`](./02-network-isolation.md)) voraus.

**Parallelität:** Präfix `03` gemeinsam mit [`03-squid-config.md`](./03-squid-config.md) —
das Compose-/Env-Wiring ist unabhängig vom Authoring der `squid.conf`/Allowlist.

Querverweise: „§x" → [`../README.md`](../README.md).

---

## 1. `docker-compose` — Service & Mounts

```yaml
services:
  forward-proxy:
    build: ./forward-proxy           # squid mit SSL-Support (Basis-Image)
    networks: [agent-net, egress-net]
    volumes:
      # host-editierbare Config, read-only (zentraler config/-Ordner, README §11):
      - ./config/squid.conf:/etc/squid/squid.conf:ro
      - ./config/allowlist.txt:/etc/squid/allowlist.txt:ro
      - ./logs/squid:/var/log/squid     # Bind-Mount: Egress-Audit (ohne Docker-Tools lesbar)
    read_only: true
    tmpfs: [/var/spool/squid, /tmp]   # Squid braucht etwas Schreib-Scratch
    healthcheck:
      test: ["CMD", "squidclient", "-h", "127.0.0.1", "mgr:info"]
    restart: unless-stopped
```

- **`read_only: true` + `tmpfs`** für die wenigen Squid-Schreibpfade → minimale
  Angriffsfläche, kein persistenter Disk-State außer dem `./logs/squid`-Bind-Mount.
- **`config/squid.conf` + `config/allowlist.txt`** read-only aus dem zentralen
  `config/`-Ordner gemountet (README §11) → der Nutzer editiert sie auf dem Host;
  Änderung wirkt per `squid -k reconfigure` ohne Image-Rebuild
  ([`03-squid-config.md`](./03-squid-config.md)). **Keine Secrets** in `config/`.

---

## 2. Agent-Env

```yaml
  claude-dev-env:
    networks: [agent-net]
    environment:
      - http_proxy=http://forward-proxy:3128
      - https_proxy=http://forward-proxy:3128
      - no_proxy=gitlab-warden        # GitLab läuft über den Warden, nicht den Proxy
```

- Der Agent hängt **nur** an `agent-net` → keine eigene Internet-Route
  ([`02-network-isolation.md`](./02-network-isolation.md)).
- **`no_proxy=gitlab-warden`** nimmt den GitLab-Pfad bewusst vom Forward-Proxy aus — der
  läuft über den Warden (W3).

---

## 3. Was über denselben Pfad läuft

- **Build-Egress** (cargo/npm/pip/conan) läuft über **denselben** Proxy/Allowlist —
  getrennte Mechanik ist unnötig.
- Claude Codes eingebaute **WebSearch** läuft serverseitig über Anthropic, **nicht** über
  diesen Proxy → immer verfügbar, null lokales Exfil-Risiko (§6.6).

---

## 4. Definition of Done

- [ ] `forward-proxy`-Service in `docker-compose` an `agent-net` + `egress-net`,
      read-only + tmpfs, Healthcheck grün.
- [ ] Agent-Env `http(s)_proxy` + `no_proxy=gitlab-warden` gesetzt; Agent nur an `agent-net`.
- [ ] Paket-Install über den Proxy erfolgreich, GitLab-Verkehr läuft am Proxy vorbei.
