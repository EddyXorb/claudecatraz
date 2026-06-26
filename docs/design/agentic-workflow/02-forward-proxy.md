# 02 — Forward-Proxy — Umsetzungsplan (Übersicht)

Konkretisierung des Research-/Build-Egress aus [`README.md`](./README.md) §6.6. Der
Forward-Proxy ist der **zweite, vom Warden getrennte** Egress-Punkt: Über ihn darf der
Agent zum Recherchieren und Bauen ins Internet — aber nur zu einer kuratierten
Domain-Allowlist. Er hält **keine** Credentials und ist **keine** R1–R6-Komponente; er
adressiert allein **Exfiltration & Supply-Chain** (§3, „Internet ≠ GitLab-Macht").

Status: **Implementierungs-Entwurf.** Dieser Plan ist in **Teilabschnitte** zerlegt; sie
liegen im Ordner [`02-forward-proxy/`](./02-forward-proxy/). Dieses Dokument ist nur noch
die **Kurzzusammenfassung + Wegweiser**.

---

## Was gebaut wird (in einem Satz)

Ein **Squid**-Container als reiner Allowlist-Egress: HTTPS wird per **SNI-peek + splice
ohne TLS-Aufbruch** gefiltert (kein CA im Agenten), Plain-HTTP per `dstdomain`,
**default-deny**, keine Credentials, alles in `access.log` auditiert. Der Agent erreicht
das Internet **nur** über diesen Proxy (Research/Build) bzw. den Warden (GitLab); seine
eigene Internet-Route ist per `internal`-Netz strukturell gekappt.

---

## Teilabschnitte (interne Reihenfolge)

Das Zahlenpräfix gibt die **Reihenfolge innerhalb des Forward-Proxy-Plans** an
(unabhängig von der projektweiten `01/02/03`-Nummerierung in der README);
**gleiches Präfix = parallel umsetzbar.**

| Stufe | Teil | Inhalt |
| ----- | ---- | ------ |
| **01** | [`01-scope-and-decisions.md`](./02-forward-proxy/01-scope-and-decisions.md) | Auftrag & Abgrenzung, Produktwahl (Squid), TLS-Grundsatzentscheidung (kein MITM), Detailfragen |
| **02** | [`02-network-isolation.md`](./02-forward-proxy/02-network-isolation.md) | Netz-Topologie, `agent-net internal`, DNS-dicht, Fail-closed-Bezug (geteilt mit Warden) |
| **03** | [`03-squid-config.md`](./02-forward-proxy/03-squid-config.md) | `squid.conf` (default-deny, SNI-peek) + `allowlist.txt` |
| **03** | [`03-deployment.md`](./02-forward-proxy/03-deployment.md) | `docker-compose`-Service + Agent-Env (`http(s)_proxy`/`no_proxy`) |
| **04** | [`04-logging.md`](./02-forward-proxy/04-logging.md) | `access.log` (audit-Format, SNI), Anbindung an Observability |
| **04** | [`04-testing.md`](./02-forward-proxy/04-testing.md) | Allowlist-/Umgehungs-Tests, Einordnung in die Red-Team-Suite |

**Begründung der Stufen:** **01** sind die Grundsatzentscheidungen (Produkt, kein
TLS-MITM) — Lesestoff, kein Build. **02** ist die Netz-Umstellung, Voraussetzung für alles
Weitere (und mit dem Warden geteilt). **03** baut den filternden Proxy: `squid.conf`/
Allowlist und das Compose-/Env-Wiring sind voneinander unabhängig → gleiches Präfix. **04**
ist Betrieb/Nachweis (Logging, Tests), beides unabhängig → gleiches Präfix.

---

## Einordnung im Gesamtprojekt

- **Abhängigkeit:** gehört zur projektweiten Stufe **02** und setzt Stufe **01** voraus
  (Token-Entfernung; die GitLab-native Schicht ist für den Proxy irrelevant). Unabhängig
  vom [`02-warden.md`](./02-warden.md) — beide `02`-Container sind parallel baubar, teilen
  sich aber die Netz-Umstellung aus
  [`02-network-isolation.md`](./02-forward-proxy/02-network-isolation.md).
- **Kein R1–R6-Bezug:** adressiert ausschließlich Exfiltration/Supply-Chain (§3, §6.6),
  nicht die GitLab-Policy.
- **Tests:** der Exfil-Block ist Fall A11 der übergreifenden Red-Team-Suite
  ([`03-testing-redteam.md`](./03-testing-redteam.md)); Logs fließen optional in
  [`03-observability.md`](./03-observability.md).
