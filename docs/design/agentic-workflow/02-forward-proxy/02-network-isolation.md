# 02-Forward-Proxy · 02 — Netz-Topologie & Isolation

Teil von [`../02-forward-proxy.md`](../02-forward-proxy.md). Die **Voraussetzung** für den
gesamten Egress-Plan: Der Agent darf **keine** eigene Internet-Route haben; sein einziger
Research-/Build-Pfad ist der Forward-Proxy. Setzt §6.1 um und sichert zugleich das
Fail-closed-Verhalten des Warden (§6.11).

Querverweise: „§x" → [`../README.md`](../README.md), „W§x" → [`../02-warden.md`](../02-warden.md).

> **Geteilte Infrastruktur.** Die hier beschriebene Netz-Umstellung (`agent-net` →
> `internal: true`, neues `egress-net`) ist **dieselbe**, die auch der Warden
> (W3/W12) braucht. Wer von beiden `02`-Plänen zuerst landet, führt sie ein; der andere
> hängt sich nur an. Daher hier vollständig beschrieben, im Warden-Plan referenziert.

---

## 1. Topologie

```
   ┌────────────────────┐   http(s)_proxy   ┌────────────────────┐
   │  claude-dev-env     │── CONNECT/GET ───▶│   forward-proxy     │── egress-net ─▶ Internet
   │  (Agent)            │   :3128           │   (Squid, Allowlist)│   (allowlisted)
   │  NUR agent-net      │                   │  KEINE Credentials │
   └────────────────────┘                   └────────────────────┘
        agent-net (internal: true)            agent-net + egress-net
```

- **`agent-net` bleibt `internal: true`** → der Agent hat **keine** eigene Internet-Route.
  Sein einziger Internet-fähiger Nachbar für Research/Build ist der Forward-Proxy (GitLab
  separat über den Warden).
- Der Forward-Proxy hat als einziger (neben dem Warden) Zugang zu `egress-net`.

---

## 2. DNS — strukturell gegen Tunneling

- **Squid löst selbst auf** (über den Docker-Resolver / einen konfigurierten Upstream).
- Der Agent braucht und bekommt **keine** direkte DNS-Route nach außen → das schließt
  **DNS-Tunneling** als Exfil-Kanal strukturell.
- Optional `dns_nameservers` in Squid hart setzen, damit die Auflösung deterministisch
  bleibt.

---

## 3. Verhältnis zum Warden-Fail-closed

Weil `agent-net` `internal: true` ist, kann der Agent **bei keinem** Ausfall auf einen
Direktzugriff „durchfallen" — weder zu `gitlab.com` (Warden-Pfad) noch ins offene Internet
(Proxy-Pfad). Das ist der entscheidende Punkt aus §6.11: Der gefährliche Fail-open-Fall ist
**per Netz-Topologie** unmöglich, nicht per Konfiguration. Diese Netz-Umstellung ist damit
zugleich ein Sicherheitsbaustein des Warden, nicht nur des Proxys.

---

## 4. Definition of Done

- [ ] `agent-net` auf `internal: true` umgestellt; `egress-net` eingeführt.
- [ ] Agent hängt **nur** an `agent-net` → kein direkter Internet-/DNS-Pfad
      (verifiziert: direkter `curl`/`dig` nach außen schlägt fehl).
- [ ] Forward-Proxy (und Warden) hängen an `egress-net`.
