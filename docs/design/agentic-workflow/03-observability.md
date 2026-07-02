# 03 — Transparenz & Observability

Der Warden ist die **einzige** Stelle, durch die GitLab-Verkehr fließt → der natürliche,
vollständige Audit-Punkt. Dieser Plan macht das Audit-Log **im Browser lesbar**.

Status: **Umgesetzt (Stufe 1).** Querverweise „§x" → README, „W§x" →
[`02-warden.md`](./02-warden.md), „F§x" → [`02-forward-proxy.md`](./02-forward-proxy.md).
**Additiv** — das System ist auch ohne diesen Ausbau sicher; die JSONL-Logs bleiben die
Quelle der Wahrheit.

---

## O.0 Entscheidungen (2026-06-27)

- **Stufe-1-Viewer ist die Lösung.** Die statische, read-only HTML-Seite mit den Events
  (O.2) genügt für Einzel-Betrieb/Debugging dauerhaft.
- **Kein `.txt`-Render.** Verworfen — JSONL + Viewer reichen; ein zweiter Render-Pfad
  bringt keinen Mehrwert.
- **Kein Loki/Grafana.** Stufe 2 (Dashboards/Alerting) wird **nicht** verfolgt. Falls je
  gewünscht, ist sie additiv nachrüstbar (Promtail/Alloy tailt das JSONL inode-folgend),
  ohne den Warden zu ändern.

---

## O.1 Audit-Quellen

Zwei unabhängige, lokale Zeilen-Logs — getrennt geschrieben, gemeinsam betrachtet:

| Quelle | Datei | Inhalt | erzeugt in |
| ------ | ----- | ------ | ---------- |
| Warden-Audit | `audit.jsonl` | jede API-/git-Entscheidung mit Regel R1–R6 | W11 / §6.8 |
| Forward-Proxy-Audit | `squid access.log` (audit-Format) | jede Egress-Verbindung mit SNI | F9 |

GitLab Audit Events ergänzen serverseitig (kein lokaler Ausbau nötig).

---

## O.2 Stufe-1-Viewer (umgesetzt)

Statische, read-only Webseite, die das JSONL im Browser filtert — ohne Build-Schritt,
ohne Zusatz-Container. Implementiert in `warden/warden/app.py`:

- **Admin-Port `9090`** (`create_admin_app`, W3, `admin-net`) — **nie** auf `agent-net`
  erreichbar, nur Lesen.
- Lädt das JSONL über den read-only Endpoint `/audit` (Tail), rendert es als Tabelle und
  filtert nach **Kanal / Entscheidung / Regel / Projekt**.
- **Highlight** sicherheitsrelevanter Ereignisse: `deny` mit `rule=R4` (Merge-Versuch) und
  Quoten-Ablehnung `R5` werden optisch hervorgehoben.
- Auto-Reload alle 30 s.

---

## O.3 Warden-Log-Format (verbindlich, §6.8)

- **JSONL als Quelle der Wahrheit** — ein JSON-Objekt pro Zeile. Felder: `ts`, `schema`
  (Audit-Schema-Version, ab §06-migration.md Schritt 2; fehlt das Feld, ist die Zeile
  Version 1, das historische unversionierte Format — siehe O.6), `channel` (`api`|`git`),
  `correlation_id`, `decision` (`allow`|`deny`), `rule` (R0–R6, zentral in `warden/rules.py`
  definiert), `project`, methoden-/ref-spezifische Felder, `upstream_status`, `latency_ms`,
  `bytes`, Quoten-Stand (`open_mrs`, `open_branches`, `writes_last_hour`).
- **Redaction per Feld-Allowlist** (nicht Blocklist): `Authorization`-Header und Token-Werte
  werden **nie** geloggt — alles außerhalb der Allowlist fällt by construction weg.

---

## O.4 Race-Freiheit (§6.8)

- **Genau ein Schreiber:** alle Log-Writes laufen durch *einen* Logger im Warden,
  serialisiert über asyncio-Queue + einzelnen Writer-Task (W11) → keine verschränkten Zeilen.
- **Atomare Zeilen:** jeder Eintrag als eine vollständige Zeile in einem Write (`O_APPEND`).
- **Leser sind strikt read-only**, eigener Pfad/Port (9090), beeinflussen die Policy nie.
- **Logging blockiert die Policy nicht:** schlägt das Schreiben fehl, wird die Entscheidung
  trotzdem durchgesetzt, Fehler auf stderr (fail-safe).

Squid (`access.log`) hat denselben Einzel-Schreiber-Charakter; Rotation per
`squid -k rotate`/logrotate (F9).

---

## O.5 Definition of Done

- [x] Warden schreibt redactetes JSONL (Feld-Allowlist), genau ein Schreiber, `O_APPEND`.
- [x] Stufe-1-Viewer auf Admin-Port `9090`, read-only, nicht auf `agent-net`
      (Filter Kanal/Entscheidung/Regel/Projekt, Highlight R4/R5).
- [ ] Log-Assertions in der Testsuite (jede Entscheidung erzeugt Eintrag mit korrekter
      Regel, **kein** Token im Log) — siehe [`03-testing-redteam.md`](./03-testing-redteam.md).
- [ ] rename+reopen-Rotation des JSONL (größen-/zeitbasiert, gzip alter Generationen) —
      offen, additiv; betrifft nur den Writer, nicht den Viewer.

Verworfen: `.txt`-Render, Loki/Grafana (siehe O.0).

---

## O.6 Schema-Versionierung & Kompat-Fenster (§06-migration.md Schritt 2)

- **`schema` (int) im Audit-Event** (`audit.AUDIT_SCHEMA_VERSION`, aktuell `2`): Version 1
  ist das historische Format ohne dieses Feld; Version 2 fügt es hinzu und ist zugleich der
  Schritt, der Tag-Push/Branch-Delete von R2 auf R4 umzieht (B3) — eine audit-sichtbare
  Änderung, deshalb an den Schema-Bump gekoppelt.
- **Kompat-Fenster:** Der Stufe-1-Viewer (`warden/warden/static/viewer.html`) zeigt sowohl
  Zeilen mit als auch ohne `schema`-Feld an — eine fehlende Version wird als „legacy"
  dargestellt statt zu brechen. `catraz observe --audit` (`src/catraz/commands/observe.py`)
  tailt die Datei roh (kein JSON-Parsing) und ist von Feldänderungen ohnehin unberührt.
- Jeder künftige Rename (claude→agent, channel→guard, §06 Schritt 6/F11) muss den
  Schema-Wert erneut erhöhen — kein Rename ohne Versions-Bump (Anti-Ziel, §06.2).
