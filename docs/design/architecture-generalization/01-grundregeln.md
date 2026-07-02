# 01 — Grundregeln: Axiome und Meta-Regelwerk

Die Axiome, aus denen sich der bestehende Code bereits implizit ableitet — jetzt explizit,
damit jede Generalisierung an ihnen gemessen werden kann. (Gestrafft nach Röst-Runde 1:
ehemals A2+A3 zusammengelegt, die Pipeline von „Axiom" zu „Implementierung" abgerüstet,
zwei fehlende Axiome ergänzt.)

## A. Die Axiome

### A1 — Default-Deny ist unverhandelbar
Alles nicht explizit Erlaubte ist verboten. Kein Plugin, keine Config, kein Guard darf diese
Eigenschaft aufweichen können. Erweiterungspunkte dürfen nur zusätzliche *Allow*-Regeln aus
geprüften Bausteinen komponieren — nie Deny-Regeln entfernen, nie neue Prüf-Logik einführen.

### A2 — Mechanismus ist Code, Policy ist Daten; Config kann nur verengen
Parser, Proxy-Transport und Check-Prädikate sind Code: getestet, versioniert, reviewt.
*Welche* Endpoints mit *welchen* Checks und Parametern erlaubt sind, ist Daten (Tabelle/TOML).
Daten dürfen Mechanismen nur **referenzieren und parametrisieren**, niemals Code enthalten
oder laden. Schärfstes Korollar: **Invarianten sind Code** — was nie erlaubt sein darf
(Merge, Tag-Erzeugung, `DROP`/`GRANT`), ist einkompiliert und von Config weder überschreibbar
noch verschattbar. Config kann enger machen, nie weiter.

### A3 — Invarianten sind kanalübergreifend
Eine verbotene Capability ist auf **allen** Kanälen verboten, nicht nur auf dem, wo sie
zuerst auffiel. Der heutige Code verletzt das: das Tag- und Branch-Delete-Verbot lebt
ausschließlich im git-Pfad (`check_ref`); die REST-Seite kennt es nicht (→ Befund B2).
Konsequenz: Invarianten werden auf einem **normalisierten Intent** durchgesetzt
(Capability-Ebene, §03), nicht pro Kanal nachgebaut. Ehrlicher Geltungsbereich (Röst-Runde
2): Capabilities modellieren *Wirkungen*, also Write-artiges; Reads werden nicht auf
Capabilities abgebildet, sondern vom Inhalts-Gate des Read-Pfads regiert (M1/M6, Befund B1:
Metadaten frei, Repository-Inhalt nur aus erlaubten Projekten). Und:
ein Kanal, der gar nicht modelliert ist, bleibt **komplett zu** — GitLabs GraphQL
(`/api/graphql`) ist heute nur zufällig nicht geroutet und darf nie „aus Bequemlichkeit"
durchgereicht werden, bevor er ein eigener Guard mit eigener Capability-Ableitung ist
(Befund B5).

### A4 — Purer Kern, unreine Ränder
`decide(intent, state, policy) → Decision` bleibt eine pure Funktion ohne I/O. Alles Unreine
(Transport, Upstream-Lookups, State, Audit) liegt an den Rändern. Das ist der Grund, warum
die Policy heute direkt unit-testbar ist — die Eigenschaft, die jeder neue Guard erben muss.

### A5 — Reihenfolge-Invarianten sind heilig
Drei Sequenz-Eigenschaften sind sicherheitstragend und müssen an genau einer Stelle erzwungen
werden: (1) Deny schließt kurz — nach einem Deny wird nichts mehr weitergeleitet;
(2) Writes werden **vor** dem Upstream-Call verbucht (§6.11 fail-safe);
(3) **jede** Entscheidung wird auditiert, auf jedem Ausgangspfad.
Die Kernel-Pipeline (§03) ist die *Implementierung* dieses Axioms, nicht selbst das Axiom.

### A6 — Credential-Isolation ist Topologie, nicht Disziplin
Der Agent hält kein Credential der bewachten Ressource; der einzige Netzweg zur Ressource
führt durch ihren Guard (fail-closed, wenn der Guard fehlt). Generalisiert wörtlich: *jede*
neue Ressource bekommt ihren Guard als alleinigen Credential-Halter. Ehrliche Grenze: solange
mehrere Guards in **einem Prozess** laufen, ist die Isolation zwischen ihren Credentials nur
Disziplin — ab dem zweiten credential-haltenden Guard gilt deshalb Prozess-Trennung (§03.6).

### A7 — Jede Entscheidung trägt eine Regel-ID und wird auditiert
`Decision` referenziert eine Regel aus einem enumerierbaren, dokumentierten Vokabular. Kein
anonymer Allow/Deny. Bei mehreren Guards werden Regel-IDs namespaced (`gitlab.R4`, `db.R4`);
für kernel-erzwungene Entscheidungen (Mode-Gate, Ressourcen-Allowlist, Capability-Invarianten)
ist ein eigener Namespace reserviert (`core.mode`, `core.allowlist`, `core.capability`) —
ein Capability-Deny als `gitlab.R4` zu loggen wäre ab dem zweiten Guard kategorial falsch.
Die IDs müssen die Meta-Regeln sauber partitionieren — heute tun sie das nicht (Befund B3).

### A8 — Auflistbarkeit schlägt Ausdrucksmächtigkeit
Enumerierbare Allowlists statt Wildcards/Regex-DSLs (heutiges §6.10, `allowed_projects`).
Eine Policy-Datei muss von einem Menschen in einer Minute reviewbar sein. Gilt auch für jeden
neuen Erweiterungspunkt: lieber zehn explizite Zeilen als ein cleveres Muster.

### A9 — Fail-closed bei Ungewissheit
Ungeklärter Zustand bedeutet Deny, nie Allow: gelockter Quota-State bis zum Reconcile,
nicht auflösbare Ownership (`mr_owner_ok=None`), unlesbare Secrets, kaputtes TOML — überall
antwortet das System mit Verweigern bzw. Nicht-Starten, nie mit „dann halt offen". Der Code
lebt das bereits durchgängig (`state.locked`, `ConfigError`); als Axiom benannt, damit es
kein neuer Guard versehentlich anders macht.

### A10 — Zwei unabhängige Schichten
Guard (Code, primär) + plattformnative Restriktionen (Backstop, zero-code: Protected
Branches, Token-Scopes — bzw. DB-Rollen/Grants). Ein neuer Ressourcentyp ist erst
vollständig, wenn *beide* Schichten benannt sind. Wo die Plattform starke native Primitive
hat (Postgres-Rollen!), darf der native Layer sogar primär sein und der Guard „nur" Audit +
Quoten + Mode liefern. Die Gewichtung ist pro Ressource verschieblich — das muss die
Abstraktion hergeben.

### A11 — Der Agent ist austauschbare, untrusted Nutzlast
Kein Sicherheitsversprechen hängt am konkreten Modell oder CLI. Agent-spezifisch sind genau
drei Dinge: Installation (Image-Layer), Credential-Abbildung, Aufruf-Konvention. Alles davon
gehört in einen Adapter — die Sicherheitsarchitektur ist agent-agnostisch.

---

## B. Das Meta-Regelwerk: was von R0–R6 wirklich generalisiert

Die GitLab-Regeln R0–R6 sind teils Instanzen ressourcen-agnostischer Meta-Regeln — aber
nicht alle. Nach Röst-Runde 1 ehrlich zweigeteilt:

### B.1 Harter Kern (generalisiert bedingungslos)

| Meta | Prinzip | GitLab-Instanz (heute) | Postgres-Instanz (später) |
| ---- | ------- | ---------------------- | ------------------------- |
| **M0** | Mode-Gate: `off` \| `read-only` \| `read-write` pro Ressource | `GITLAB_MODE` (R0) | `DB_MODE` |
| **M5** | Quoten + Rate-Limits, fail-safe bei ungeklärtem State (A9) | max MRs/Branches/Writes-pro-h (R5); als Erweiterung ein **Read-Volumen-Budget** (Bytes/h, distinct Projekte/h) gegen Massen-Exfiltration durch erlaubte Reads | Statements/h, max Rows pro Write |
| **M6** | Credential- & Netz-Isolation + Ressourcen-Allowlist | kein Token im Agenten, `allowed_projects` (R6) — *für Reads gilt sie als **Inhalts**-Grenze (Metadaten sind frei) und erst nach dem B1-Fix, siehe Befund B1* | kein DB-Credential im Agenten, `allowed_databases` |
| **MA** | Vollständiges Audit mit Regel-ID (A7) | JSONL-Audit | Statement-Log |

### B.2 Plattformabhängig (generalisiert nur, wo die Plattform mitspielt)

| Meta | Prinzip | Voraussetzung an die Plattform | GitLab | Postgres |
| ---- | ------- | ------------------------------ | ------ | -------- |
| **M1** | Reads mit Least-Privilege-**Read-Credential** | natives Read-only-Credential existiert | Read-Token (R1) — *aber siehe Befund B1: die Allowlist greift auf dem Read-Pfad nicht überall* | read-only DB-Rolle (stark) |
| **M2** | Writes nur im **eigenen Namensraum** | Namensraum-Begriff existiert (Branches, Schemas) | `branch_prefix` (R2) | Grants nur auf Schema `agent_*` |
| **M3** | Aktionen nur auf **eigenen Objekten** | Server trackt Autorschaft | MR-`author.id` (R3) | **keine Instanz** — Postgres kennt kein „created_by" pro Row; nur via RLS/Schema-Design des bewachten Systems |
| **M4** | Irreversible/privilegienerhöhende **Capabilities: niemals** | Capability aus dem Request ableitbar | Merge, Tag-Erzeugung, Branch-Delete | `DROP`, `TRUNCATE`, `GRANT`, `ALTER ROLE` |

**Konsequenzen dieser Zweiteilung:**

- M3 ist eine **Forge-Regel**, keine Meta-Regel. Ein Guard, dessen Plattform keine
  Autorschaft trackt, deklariert den begründeten Verzicht — und kompensiert, wo möglich,
  über M2 (engerer Namensraum) und den nativen Layer (A10).
- M4 wird erst durch die **Capability-Ebene** (§03.4) kanalübergreifend durchsetzbar (A3);
  heute ist es über R2/R4 und zwei Kanäle verschmiert (Befund B2/B3).
- Ein neuer Guard deklariert für **jede** Meta-Regel entweder seine Instanz oder den
  begründeten Verzicht, plus die native Backstop-Schicht (A10). Ohne das ist er unvollständig.
