# 02-Forward-Proxy · 04 — Teststrategie

Teil von [`../02-forward-proxy.md`](../02-forward-proxy.md). Weist nach, dass die Allowlist
hält und Umgehungen abgewehrt werden. Setzt den laufenden Proxy
([`03-deployment.md`](./03-deployment.md)) voraus.

**Parallelität:** Präfix `04` gemeinsam mit [`04-logging.md`](./04-logging.md).

Querverweise: „§x" → [`../README.md`](../README.md), übergreifende Suite →
[`../03-testing-redteam.md`](../03-testing-redteam.md).

---

## 1. Testfälle

| Was | Erwartung |
| --- | --------- |
| `pip install`/`npm install`/`cargo fetch` über den Proxy | ✅ erfolgreich (Allowlist deckt transitiv) |
| `curl https://allowlisted` | ✅ splice, durchgereicht |
| `curl https://evil.example.com` | ❌ `ssl_bump terminate` / deny |
| `curl http://evil.example.com` | ❌ `http_access deny all` |
| CONNECT zu allowlistetem Host:22 | ❌ `!safe_ports` |
| CONNECT zu IP-Literal | ❌ kein `dstdomain`-Treffer |
| **Red-Team:** Exfil-POST zu nicht-allowlistetem Host | ❌ block + im `access.log` sichtbar |

---

## 2. Einordnung in die Gesamtsuite

Diese Fälle sind Teil der `tests/redteam/`-Suite
([`../03-testing-redteam.md`](../03-testing-redteam.md), Fall A11): Der
Exfil-Block-Test prüft genau diesen Proxy. Egress-Tests laufen im E2E-Stage der CI (§8.4).

---

## 3. Definition of Done

- [ ] Alle Tabellenfälle aus §1 grün.
- [ ] Exfil-Block-Test (A11) in `tests/redteam/` verankert und in CI.
- [ ] Clean-Build-Verifikation der Allowlist bestanden (keine stillen Build-Brüche).
