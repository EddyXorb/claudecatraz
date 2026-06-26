# 03 — Transparenz & Observability

Konkretisierung des Logging-/Transparenz-Ausbaus aus [`README.md`](./README.md) §6.8.
Der Warden ist die **einzige** Stelle, durch die GitLab-Verkehr fließt → der natürliche,
vollständige Audit-Punkt. Dieser Plan macht das Audit-Log **im Browser lesbar** — in zwei
Ausbaustufen — und legt die Race-Freiheit verbindlich fest.

Status: **Implementierungs-Entwurf.** Querverweise „§x" → README, „W§x" →
[`02-warden.md`](./02-warden.md), „F§x" → [`02-forward-proxy.md`](./02-forward-proxy.md).

**Parallelität:** Präfix `03` gemeinsam mit
[`03-testing-redteam.md`](./03-testing-redteam.md). Beide setzen die `02`-Komponenten
(Warden, Forward-Proxy) voraus, sind aber untereinander unabhängig (Observability liest
Logs, Tests fahren den Stack) → gleichzeitig machbar. **Additiv** — das System ist auch
ohne diesen Ausbau sicher; die JSONL-Logs bleiben die Quelle der Wahrheit.

---

## O.1 Audit-Quelle: zwei Logs, ein Schema-Prinzip

Es gibt **zwei** unabhängige Audit-Quellen — sie werden **nicht** vermischt, aber gemeinsam
betrachtet:

| Quelle | Datei | Inhalt | erzeugt in |
| ------ | ----- | ------ | ---------- |
| Warden-Audit | `warden-audit.jsonl` | jede API-/git-Entscheidung mit Regel R1–R6 | W11 / §6.8 |
| Forward-Proxy-Audit | `squid access.log` (audit-Format) | jede Egress-Verbindung mit SNI | F9 |
| GitLab Audit Events | (GitLab-seitig) | Bot-Aktionen serverseitig | N.8 |

Die ersten beiden liegen lokal als maschinenlesbare Zeilen-Logs vor und sind die Basis
dieses Plans. GitLab Audit Events ergänzen die serverseitige Sicht (kein lokaler Ausbau
nötig).

---

## O.2 Warden-Log-Format (verbindlich, aus §6.8)

- **Quelle der Wahrheit: JSONL** — ein JSON-Objekt pro Zeile. Felder:
  `ts` (UTC ms), `channel` (`api`|`git`), `corr_id`, `decision` (`allow`|`deny`),
  `rule` (R1–R6), `project`, methoden-/ref-spezifische Felder, `upstream_status`,
  `latency_ms`, `bytes`, Quoten-Stand (`open_mrs`, `open_branches`, `writes_last_hour`).
- **Redaction:** `Authorization`-Header und Token-Werte werden **nie** geloggt — per
  Feld-**Allowlist** serialisieren (nicht per Blocklist), damit nichts versehentlich
  durchrutscht.
- **Menschlich lesbar:** zusätzlich gerendertes `.txt` (eine ausgerichtete Zeile je
  Ereignis), parallel geschrieben oder on-demand aus dem JSONL erzeugt.

---

## O.3 Race-Freiheit (explizite Anforderung, §6.8)

- **Genau ein Schreiber:** alle Log-Writes laufen durch *einen* Logger im Warden,
  serialisiert über eine asyncio-Queue + einzelnen Writer-Task (W11). Nebenläufige
  Requests können Zeilen nicht verschränken.
- **Atomare Zeilen:** jeder Eintrag als **eine** vollständige Zeile in einem Write
  (`O_APPEND`). Leser sehen immer ganze Zeilen, nie Fragmente.
- **Rotation per rename+reopen** (nicht `copytruncate`) → tailende Leser folgen dem Inode
  statt auf eine truncate-Lücke zu treffen. Größen-/zeitbasiert (z. B. 50 MB / täglich),
  N Generationen, gzip der alten.
- **Leser sind strikt read-only**, eigener Pfad/Port, beeinflussen die Policy nie.
- **Logging blockiert die Policy nicht:** schlägt das Schreiben fehl, wird die
  Entscheidung trotzdem durchgesetzt, Fehler auf stderr (fail-safe).

Squid (`access.log`) hat denselben Einzel-Schreiber-Charakter; Rotation per
`squid -k rotate`/logrotate (F9).

---

## O.4 Stufe 1 — leichtgewichtiger Log-Viewer

Ziel: ohne zusätzliche Infrastruktur das JSONL im Browser filtern.

- **Statische Read-only-Seite** (HTML + etwas JS), die das JSONL lädt und nach
  **Kanal / Regel / Entscheidung / Projekt / Zeit** filtert/rendert.
- Ausgeliefert vom Warden auf dem **Admin-Port `9090`** (W3, `admin-net`), **nie** auf
  `agent-net` erreichbar. Nur Lesen, kein Schreibzugriff.
- Minimal: kein Build-Schritt nötig (eine Datei), kein zusätzlicher Container.
- Highlight-Regeln für sicherheitsrelevante Ereignisse: jeder `deny` mit `rule=R4`
  (Merge-Versuch) und jede Quoten-Ablehnung (`R5`) optisch hervorheben.

**Wann das reicht:** für Einzel-Betrieb/Debugging genügt Stufe 1 meist dauerhaft.

---

## O.5 Stufe 2 — Grafana + Loki (bei Bedarf)

Sobald Dashboards/Zeitreihen/Alerting gewünscht sind:

```
warden  ──JSONL──▶  Promtail/Alloy  ──▶  Loki  ──▶  Grafana
squid   ──access.log─┘
```

- **Promtail/Alloy** tailt beide Logs (Inode-folgend, passt zur rename+reopen-Rotation).
- **Loki** indexiert; **Grafana**-Dashboards:
  - Requests/h je Kanal, **Deny-Quote je Regel**, Quoten-Auslastung (offene MRs/Branches,
    Writes/h gegen Limit).
  - Egress-Ziele/Volumina aus dem Squid-Log (Exfil-Erkennungsnetz, §6.10).
  - **Alerts:** Merge-Versuch (`R4 deny`), Rate-Limit-Treffer (`R5`), Exfil-Block am Proxy,
    ungewöhnliche Lese-Spitzen.
- Mehr Container, dafür Queries/Alerting. **Additiv:** das JSONL bleibt Quelle der
  Wahrheit, der Stack liest nur.

Beide Stufen **lesen nur** — der Warden bleibt alleiniger Schreiber, die Race-Freiheit aus
O.3 gilt unverändert.

---

## O.6 Definition of Done

- [ ] Warden schreibt redactetes JSONL (Feld-Allowlist), ein Schreiber, rename+reopen-Rotation.
- [ ] `.txt`-Render verfügbar (parallel oder on-demand).
- [ ] Stufe-1-Viewer auf Admin-Port `9090`, read-only, nicht auf `agent-net`.
- [ ] Log-Assertions in der Testsuite (jede Entscheidung erzeugt Eintrag mit korrekter
      Regel, **kein** Token im Log) — siehe [`03-testing-redteam.md`](./03-testing-redteam.md).
- [ ] (Optional) Loki/Grafana mit Alerts auf R4/R5/Exfil, falls Dashboards gewünscht.
