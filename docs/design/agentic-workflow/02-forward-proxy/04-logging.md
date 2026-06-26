# 02-Forward-Proxy · 04 — Logging & Audit

Teil von [`../02-forward-proxy.md`](../02-forward-proxy.md). Der Egress-Audit-Punkt. Setzt
den laufenden Proxy ([`03-deployment.md`](./03-deployment.md)) voraus.

**Parallelität:** Präfix `04` gemeinsam mit [`04-testing.md`](./04-testing.md) — Logging
und Tests sind unabhängig.

Querverweise: „§x" → [`../README.md`](../README.md).

---

## 1. Was geloggt wird

`access.log` im `audit`-Format ([`03-squid-config.md`](./03-squid-config.md)) ist der
Audit-Punkt des Egress: **Zeitstempel, Client, Squid-Status, Methode, Ziel-URL, SNI**.

- Read-only auf einem Volume, gleiche Hygiene wie das Warden-Log (§6.8): genau **ein**
  Schreiber (Squid selbst), **keine** Bodies (wir entschlüsseln nicht → ohnehin nur
  Metadaten).
- **Rotation** per logrotate bzw. `squid -k rotate`.

---

## 2. Wozu

Auswertbar nach **ungewöhnlichen Zielen/Volumina** als nachträgliches
Exfil-Erkennungsnetz (§6.10). Der Squid-Log fließt — wie das Warden-JSONL — optional in
den übergreifenden Observability-Ausbau ein
([`../03-observability.md`](../03-observability.md)): Promtail/Alloy tailt das
`access.log`, Grafana zeigt Egress-Ziele/Volumina und alarmiert bei Exfil-Blocks.

---

## 3. Definition of Done

- [ ] `access.log` im `audit`-Format mit SNI-Feld, auf eigenem Volume, rotiert.
- [ ] Keine Bodies/Secrets im Log (strukturell, da kein Decrypt).
- [ ] (Optional) Einbindung in Grafana/Loki über [`../03-observability.md`](../03-observability.md).
