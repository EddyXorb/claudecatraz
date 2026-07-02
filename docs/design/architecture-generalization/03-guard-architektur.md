# 03 — Guard-Architektur

Die Trennung von git- und API-Proxy, die Kernel-Pipeline, die Capability-Invarianten-Ebene
und die zwei geplanten Belastungsproben (zweite Forge, Datenbank).

## 03.1 Ist-Zustand

`git_proxy.py` und `api_proxy.py` implementieren beide dieselbe implizite Pipeline von Hand,
inklusive der sicherheitskritischen Sequenzierung (A5: Write-Verbuchung *vor* dem
Upstream-Call, Audit auf jedem Pfad). `policy.decide()` verzweigt per `Channel`-Enum,
`ProxyRequest` ist die Vereinigungsmenge beider Kanäle. Das funktioniert für zwei Kanäle —
beim dritten (DB) kippt es.

## 03.2 Kernel-Pipeline

Kein Framework, sondern eine dünne Schicht, die A5 erzwingt (Template-Method im Kernel):

```python
# warden/core/guard.py — der Kernel besitzt die Pipeline, Guards liefern die Teile.
class Guard(Protocol):
    name: str                          # "git", "gitlab_api", "postgres" — Audit-Namespace (A7)
    def routes(self) -> list[Route]: ...            # Transport-Anbindung
    async def parse(self, request) -> Intent: ...   # Transport → geparste Absicht
    async def enrich(self, intent) -> Intent: ...   # unreine Lookups, die Checks deklariert haben
    def decide(self, intent, state, policy) -> Decision: ...   # PUR (A4), default-deny (A1)
    def record(self, intent, state) -> None: ...    # Quota-Verbuchung VOR forward (Kernel ruft!)
    async def forward(self, request, intent, decision) -> Response: ...  # Credential-Injektion
```

Der Kernel (`run_guarded(guard, request)`) garantiert: Mode-Gate (M0) und
Ressourcen-Allowlist (M6) zuerst, dann **Capability-Invarianten (03.4)**, dann Guard-`decide`;
Audit auf *jedem* Ausgang; `record` vor `forward`; `forward` nur nach `decision.allow`
erreichbar. Guards können die Sequenz nicht falsch bauen — sie sehen sie gar nicht.

## 03.3 Modul-Layout und die richtige Schnittlinie

```
warden/
├── core/            # Kernel: Pipeline, Decision/Intent/StateView, Capability-Invarianten,
│   │                #   Audit, State, benannte Quoten-Zähler, Mode-Gate, Ressourcen-Allowlist,
│   │                #   Check-Registry, Regel-Registry, Config-Basis
├── guards/
│   ├── git/         # git Smart-HTTP (pktline, Ref-Policing) — FORGE-AGNOSTISCH
│   ├── gitlab_api/  # GitLab-REST-Tabellen (Read + Write) + Ownership + Reconcile
│   ├── github_api/  # (später)
│   └── postgres/    # (später)
```

Die fehlende Trennung ist nicht „git vs. GitLab" als Geschwister, sondern:
**git-Protokoll-Guard (generisch) + Forge-API-Guard (pro Forge)**. pktline, Ref-Kommandos
und Branch-Prefix-Policing sind kein GitLab; forge-spezifisch sind Upstream-URL-Form und
Auth-Stil (GitLab: `oauth2:<token>` Basic; GitHub: `x-access-token:<pat>`; Gitea wieder
anders). `upstream.py` zerfällt in einen generischen Streaming-Client + pro Guard einen
kleinen `CredentialAdapter`. Geteilt zwischen git-Guard und Forge-Guard: Namespace-Policy
(`branch_prefix`), Service-Account-Begriff, Quoten-State.

**Intent statt Kanal-Union:** `ProxyRequest` wird ersetzt durch ein schlankes
`Intent`-Protokoll mit guard-spezifischen Ausprägungen (`GitPushIntent(ref_commands=…)`,
`ApiWriteIntent(path=…, fields=…)`, `SqlIntent(statements=…)`). Das `Channel`-Enum und die
`if/elif`-Verzweigung in `policy.decide()` entfallen.

## 03.4 Capability-Invarianten — die kanalübergreifende Verbotsschicht (A3)

*Übernommen aus Röst-Runde 1 (Roaster-Idee 1); schließt Befund B2 und macht §04 erst möglich.*

Jeder Guard normalisiert seinen Intent zusätzlich auf ein **kleines, geschlossenes
Capability-Vokabular** — abgeleitete Eigenschaften dessen, was der Request *bewirken würde*:

```
creates_ref · deletes_ref · creates_tag · merges · escalates_privilege ·
writes_outside_namespace · destroys_data
```

Der Kernel hält **eine** einkompilierte `FORBIDDEN`-Menge und denied jeden Intent, dessen
Capabilities sie schneiden — vor jeder Allow-Regel, vor jeder TOML-Zeile, auf jedem Kanal.
Damit ist „no tags" nicht mehr eine Zeile in `check_ref`, sondern eine Eigenschaft des
Systems: ein REST-Endpoint, der einen Tag erzeugen würde, wird abgelehnt, egal welche Felder
sein Autor geprüft hat.

**Ehrliche Kosten:** Die Trust-Kritikalität verschiebt sich auf die Vollständigkeit der
Intent→Capability-Abbildung pro Guard. Vergisst ein Guard zu deklarieren, dass ein
Release-Call einen Tag erzeugt, feuert die Invariante nicht. Diese Abbildung ist aber
kleiner, purer, testbarer Code (Golden-Tests: bekannte Requests → erwartete Capabilities) —
ein viel kleinerer trust-kritischer Kern als die heutige Streuung über fünf Module. Für
git-Refs ist die Abbildung trivial und exakt; für REST-Endpoints ist sie Teil der
(eingebauten oder Registry-) Tabellenzeile, nie der Nutzer-Config.

## 03.5 Forge-Abstraktion: GitLab → GitHub/Gitea

Ein neuer Forge-API-Guard bringt mit: Endpoint-Tabellen (Read + Write), `CredentialAdapter`,
Capability-Abbildung, Reconcile, Service-Account-Auflösung. Kern, git-Guard, Quoten, Audit
und CLI bleiben unverändert. Konkret zu entkoppeln:

- **Begriffe:** MR vs. PR, `iid` vs. `number` — bleiben im Guard; der Kern kennt nur
  Quoten-Dimensionen (M5) und Ownership (M3).
- **Reconcile** (`context.py`) → Guard-Methode; der Kernel besitzt Takt + Lock-Semantik (A9).
- **Service-Account-Auflösung** (`GET /user`) → Guard-Methode mit Vertrag „liefere die
  Identität des Write-Credentials oder None ⇒ fail-closed".
- **Namensgebung:** `mr_owned_by_claude` → `owned_by_agent`, `_list_claude_branches` →
  `_list_agent_branches`; „claude" bleibt nur als Default-Wert des Namespace-Präfixes.
  (Kein triviales Rename — siehe F11: State-Tabellen und Audit-Feld hängen dran.)

## 03.6 Prozess-Grenzen zwischen Guards

*Aus Röst-Runde 1 (Roaster-Idee 2), modifiziert übernommen.*

Solange alle Guards in einem Prozess laufen, ist die Credential-Isolation **zwischen den
Guards** nur Disziplin — ein Parser-Bug im SQL-Parser könnte das GitLab-Token lesen. Das
widerspricht dem Geist von A6. Beschluss:

- **Heute (nur GitLab):** ein Prozess, dokumentierte Grenze — kein Umbau, kein Gewinn.
- **Ab dem zweiten credential-haltenden Guard (z.B. Postgres):** Prozess-/Container-Trennung
  pro Guard. Jeder Guard-Prozess hält nur das Credential seiner Ressource; geteilt werden
  ausschließlich (a) der pure `decide`-/Capability-Kern als Library (kein I/O, kein
  Credential) und (b) ein zentraler Quota/Audit-Dienst über einen Unix-Socket. Die
  compose-Komplexität kaschiert `catraz up`; die Nutzeroberfläche ändert sich nicht.

## 03.7 Datenbank-Guard — der Testfall, der die Abstraktion ehrlich macht

Ein Postgres-Guard nach M0–M6 (der Agent bekommt `postgres://warden:5432/`, der Guard hält
die echten Credentials):

- **M1:** `SELECT` über eine **read-only DB-Rolle** — das Read-Credential *ist* der native
  Backstop (A10), symmetrisch zum Read-Token bei GitLab.
- **M2:** Write-Credential hat Grants **nur** auf Schema `agent_*` — wieder nativ.
- **M3:** **keine Instanz** (ehrlich, siehe §01/B.2) — Postgres trackt keine Autorschaft pro
  Row; Kompensation über engen M2-Namensraum + ggf. RLS im bewachten System.
- **M4:** `DROP`/`TRUNCATE`/`GRANT`/DDL als Capabilities (`destroys_data`,
  `escalates_privilege`) in der `FORBIDDEN`-Menge **und** fehlende Grants nativ.
- **M5 + MA:** Statements/h, jedes Statement geloggt — das kann nur der Guard.

**Gewichtung anders als bei GitLab (A10):** SQL-Parsing als *primäre* Sicherheitsgrenze ist
eine Falle (prepared statements, `DO`-Blöcke, Funktionen, Dialekte). Native Rollen/Grants
sind hier Layer 1; der Guard liefert Topologie (A6), Mode-Gate, Quoten, Audit und nur
**grobe Statement-Klassifikation** (read/write/ddl), keine feingranulare SQL-Policy. Genau
diese Verschiebbarkeit der Gewichtung muss die Abstraktion hergeben — deshalb ist der
DB-Guard die beste Belastungsprobe, und deshalb steht er am **Ende** des Migrationspfads:
erst generalisieren, wenn der zweite Anwender existiert.
