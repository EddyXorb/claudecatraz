# 06 — Migrationspfad und Anti-Ziele

## 06.1 Migrationspfad — kein Big Bang

Reihenfolge nach zwei Röst-Runden: **Sicherheitsbefunde zuerst** (sie sind unabhängig von
jeder Abstraktion), dann Schema-Versionierung (Voraussetzung für alles, was Namen ändert),
dann Nutzerwert und Refactorings. Jeder Schritt ist einzeln shipbar; die bestehenden Tests
sind das Verhaltens-Netz. Jeder Schritt synchronisiert `docs/design/agentic-workflow/`
(die kanonische Design-Doku, deren W-/R-Referenzen im Code stehen) mit.

**Vorarbeiten (vor bzw. parallel zu Schritt 1, Maintainer-Entscheid Clean Code): ✅ umgesetzt**

- **Clean-Code-Refactor des Warden**, verhaltenserhaltend: Audit-Event-Bau vereinheitlichen
  (F6, ohne die JSONL-Feldnamen zu ändern — das wäre Schritt 2), Viewer-HTML als
  Package-Asset (F7), Präfix-Checks deduplizieren (F10), `project_allowed` exakt matchen
  (B4), `api_proxy.handle` in kleine, einer Abstraktionsschicht zugeordnete Funktionen
  zerlegen (SRP).
- **`branch_prefixes` als Liste** (Maintainer-Entscheid): Config akzeptiert eine Liste
  erlaubter Präfixe (legacy `branch_prefix` als Ein-Element-Liste weiter gültig); ein
  einziger Namespace-Helper im Code, den R2/R3-Checks und Reconcile nutzen — keine
  verstreuten `startswith`-Aufrufe.

1. ✅ **B1 fixen: Read-Pfad scopen** *(umgesetzt: `warden/read_endpoints.py`,
   Query-Forwarding in `api_proxy`, GraphQL-403-Route)* — Schutzlinie „Inhalt, nicht Sichtbarkeit"
   (Maintainer-Entscheid): als **minimale Read-Tabelle** gebaut mit vier Kategorien
   (projekt-gebunden → Gate wie heute; projektlose Metadaten → pass; projektlose
   inhaltsfähige Endpoints wie globale/Gruppen-Blob- und Commit-Suche und `/snippets` →
   deny; unbekannt → default-deny), die Schritt 4 nur erweitert, nicht ersetzt (kein
   Rework, Röst-Runde 2). Beifang: F12 für den `scope`-Query-Parameter (Query konsistent
   in Entscheidung **und** Forwarding) und B5: `/api/graphql` explizit 403 + Audit.
   Red-Team-Tests dazu (`GET /search?scope=blobs` muss 403 sein;
   `GET /groups/<id>/projects` muss funktionieren; GraphQL muss 403 sein).
2. ✅ **Regel-Registry + Schema-Versionierung** *(umgesetzt: `warden/rules.py`,
   `audit.AUDIT_SCHEMA_VERSION`, `state.py` Migrations-Runner)* — zentrale Regel-IDs
   (R0–R6, je einer Meta-Regel M0–M6 zugeordnet) inkl. reserviertem Kernel-Namespace
   `core.*` (`rules.qualify`, vorbereitet, noch nicht im Log-Output aktiv — der bleibt
   unqualifiziert bis Schritt 6). Fixt B3: Tag-Push und Branch-Delete (`policy.check_ref`)
   loggen jetzt R4 ("irreversible Verben: niemals", M4) statt R2 — eine audit-sichtbare
   Änderung, deshalb an die neue Schema-Versionierung gekoppelt: Audit-JSONL trägt ein
   `schema`-Feld (Version 2 = dieser Schritt, Version 1 = das alte, feldlose Format), die
   State-DB trägt ihre Version in `PRAGMA user_version` (Begründung: kein Bootstrap-Problem,
   nicht mit der bestehenden `meta`-Tabelle für Anwendungszustand vermischt) plus einem
   geordneten Migrations-Runner (frische DB → aktuelle Version; bestehende unversionierte
   DB → ohne Datenverlust angehoben; zu neue Version → harter Fehler, `state.SchemaError`,
   fail-closed). Viewer und `catraz observe` bleiben kompatibel mit Zeilen ohne `schema`-Feld
   (Kompat-Fenster, verifiziert + Viewer zeigt „legacy" für Alt-Zeilen). Voraussetzung für
   claude→agent- und channel→guard-Renames (F11, Schritt 6).
3. ✅ **Capability-Invarianten-Ebene** (§03.4) *(umgesetzt: `warden/capabilities.py`,
   `WriteEndpoint.capabilities` + `api_endpoints.api_capabilities` in `api_endpoints.py`,
   Integration in `policy._decide_git`/`_decide_api`)* — fixt B2 kanalübergreifend für alle
   code-bekannten Endpoints; klein, pur, golden-getestet. Das geschlossene Vokabular
   (`creates_ref · deletes_ref · creates_tag · merges · escalates_privilege ·
   writes_outside_namespace · destroys_data`) und die einkompilierte `FORBIDDEN`-Menge
   (`deletes_ref, creates_tag, merges, escalates_privilege, destroys_data`) leben in
   `capabilities.py`; `creates_ref` (Normalfall des Agenten) und
   `writes_outside_namespace` (per-Deployment-Namensraum, von R2/R3 bereits gebändigt)
   bleiben bewusst außerhalb der Verbotsmenge, dokumentiert im Modul-Docstring.
   Intent→Capability-Abbildung: git trivial und exakt aus `RefCommand` (`git_ref_capabilities`);
   REST als statisches Feld pro `WriteEndpoint`-Zeile, ergänzt um die eine feld-abhängige
   Ausnahme (`api_capabilities`: `state_event=merge` auf der MR-Update-Zeile ⇒ zusätzlich
   `merges`, sonst würde eine statische Zeilen-Capability entweder das legitime Editieren
   verbieten oder den Merge-Alias verpassen). `policy._decide_git`/`_decide_api` prüfen die
   Capability-Menge gegen `FORBIDDEN` **vor** den kanalspezifischen Checks/Quoten — ein
   Treffer denied sofort mit R4, unabhängig davon, was `check_ref` oder eine Endpoint-Zeile
   sonst entschieden hätten. Die bestehenden Spezialfälle (`always_deny`, Tag-/Delete-Checks
   in `check_ref`) bleiben als Defense-in-depth (A10) bestehen; `test_capabilities.py` belegt
   golden-getestet, dass die Invariante auch ohne sie greift (u. a. eine hypothetische
   Endpoint-Zeile ganz ohne Checks, die trotzdem R4-denied wird). Voraussetzung für Schritt 4.
4. ✅ **Endpoint-Katalog + Check-Registry + Aktivierungs-Config** (§04.1–04.3) *(umgesetzt:
   `warden/warden/catalog/` — `checks.py`, `entries.py`, `builtin.py`, `config_parse.py`,
   `activation.py`, `startgate.py`, `report.py`; `api_endpoints.py` ist jetzt eine
   Kompat-Fassade)* — der nutzersichtbare Gewinn. Jeder Katalog-Eintrag bringt seine
   Deny-Sonden mit (§04.4), und das Startgate, das sie ausführt, ist **Teil dieses
   Schritts** — nicht ein späterer (Röst-Runde 2: sonst shipped Schritt 4 unabgesichert).
   Fixt F2 (`RegisteredCheck.needs` statt Funktions-Identität), F10 zu Ende (die
   Deduplizierung von `src_branch_prefix`/`ref_prefix` zu `field_has_prefix` war bereits
   Vorarbeiten-Stand; dieser Schritt hebt sie zusätzlich in die benannte Check-Registry,
   §04.1) und F12 (`CatalogEntry.decision_fields` — Entscheidung liest nur die deklarierte
   Lage, Body **oder** Query) nebenbei. `DEFAULT_ENABLED` ist
   exakt der vor diesem Schritt aktive Sechser-Satz (Verhaltenserhaltung); zwei zusätzliche,
   ehrlich katalogisierte, aber nicht default-aktive Einträge (`branch.create`,
   `issue.create`) demonstrieren den Katalog inkl. Golden-Tests und Deny-Sonden. Zwei
   dokumentierte Abweichungen von der Skizze in §04-policy-erweiterbarkeit.md (§04.5
   „Umsetzungsnotizen"): Audit-Markierung als eigenes Feld `enabled_via` statt `rule`-Suffix
   (Registry-Disziplin aus Schritt 2 bleibt gewahrt), und kein Taming-Mechanismus für
   FORBIDDEN-Capabilities (bewusstes YAGNI — kein aktueller Eintrag braucht ihn; kommt mit
   dem ersten, z. B. `release.create`). `catraz allow-endpoint` + eine read-only
   `/policy`-Admin-Route (`catraz doctor --section endpoints`) sind die CLI-Front dafür.
5. **Kernel-Extraktion + Intent-Split** (§03.2/03.3, inkl. `intent.writes` für das
   read-only-Gate) — reines Refactoring, von `test_api_proxy`/`test_git_proxy` abgedeckt;
   fixt F1/F3/F6; blockiert keinen Nutzerwert und wird deshalb *nicht* vorgezogen.
6. **Rename claude→agent im Warden** — jetzt trivial, weil Schritt 2 die Migrationen
   bereitstellt (State-Tabellen `claude_branches`/`claude_mrs`, Audit-Felder).
7. **Agent-Layer: Entrypoint-Zerlegung** (§05.2) — generischer Entrypoint + Claude-Adapter
   (inkl. `environ`/`render_instructions`), verhaltenserhaltend, von `tests/container/`
   abgedeckt. Danach Manifest + Profile (§05.3), `catraz`-CLI-Entkopplung und die
   **Adapter-Conformance-Harness** (§05.5) — sie ist die Abnahme dieses Schritts.
   Zusätzlich (Maintainer-Entscheid, 2026-07): **persistenter Claude-State pro Repo**
   (§05.6, `credentials.mode = "persistent"`): eigenes `claude login` im Dev-Container
   statt geteilter Token-Familie mit dem Host, `.catraz/state/claude/` **selektiv**
   writable gemountet (Credentials + Session-State, nie Settings/Hooks — A11) — fixt die
   zuverlässigen `claude-remote`-Abbrüche nach OAuth-Token-Rotation.
8. **Policy-by-Example-UX** (§04.4) — `catraz allow-endpoint --from-example`,
   Nutzer-Assertions in `.catraz/policy-tests/`. Reiner Komfort: das sicherheitstragende
   Startgate (Katalog-Sonden) kam schon mit Schritt 4.
9. **Zweiter Guard** (Forge oder DB, §03.5/03.7) — die Belastungsprobe der Abstraktion.
   Bewusst zuletzt: erst generalisieren, wenn der zweite Anwender existiert.
10. **Prozess-Trennung pro credential-haltendem Guard** (§03.6) — eigener Schritt, nicht
    Beifang von 9 (Röst-Runde 2): Container-Split, Quota/Audit-Dienst über Unix-Socket,
    compose-Umbau. Pflicht, sobald der zweite Guard ein eigenes Credential hält.

Abhängigkeiten kompakt: 1 ist frei; 2 → 6; 3 → 4 → 8; 5 ist frei (nach 3 sinnvoll);
7 ist frei; 9 braucht 5; 10 folgt 9.

## 06.2 Was man bewusst NICHT tun sollte (Anti-Ziele)

- **Kein dynamisches Plugin-Laden** (entry_points, `importlib` aus Config-/`.catraz`-Pfaden):
  Config darf nie zu Code-Ausführung führen (A2). Guards und Agent-Adapter sind einkompiliert
  bzw. mitgeliefert; Config wählt und parametrisiert.
- **Keine freien Endpoint-Zeilen in Nutzer-Config** — Config aktiviert und verengt
  Katalog-Einträge, sie definiert keine (Röst-Runde 2: jede „deklariere selbst, was der
  Endpoint kann"-Variante degeneriert zu Vertrauen in die Nutzer-Deklaration). Neue
  Endpoints sind ein kleiner Katalog-PR.
- **Keine Wildcard-/Regex-Policy-DSL** in Nutzer-Config — A8 gilt auch für neue
  Erweiterungspunkte. Zehn explizite Zeilen schlagen ein cleveres Muster.
- **GraphQL niemals durchrouten**, solange es kein eigener Guard mit eigener
  Capability-Ableitung ist (B5) — der Warden beantwortet `/api/graphql` aktiv mit 403,
  damit die Absicht im Code steht, nicht im Zufall.
- **Kein generisches Proxy-Framework** als Selbstzweck. Die Generalität kommt aus den
  Meta-Regeln und der Capability-Ebene, nicht aus unendlicher Flexibilität. Abstraktionen
  erst, wenn der zweite konkrete Anwender existiert (deshalb steht der DB-Guard am Ende).
- **Deny-Invarianten niemals konfigurierbar machen** — auch nicht „nur für Tests", auch
  nicht hinter einem Flag (A2). Tests bauen sich ihre Tabelle im Code.
- **Egress-Erweiterungen niemals automatisch übernehmen** — weder aus Agent-Profilen (§05.4)
  noch aus sonstiger Config; die Exfiltrationsgrenze wächst nur durch explizite,
  sichtbare Nutzerentscheidung.
- **Die Zweischichtigkeit (A10) nicht der Bequemlichkeit opfern** — ein Guard ohne benannten
  nativen Backstop ist ein Design-Smell, kein Feature.
- **Kein Rename ohne Schema-Versionierung** — Audit-Log und State-DB sind Produkt, nicht
  Implementierungsdetail; ein Bruch dort ist ein Bruch der Auditierbarkeit (A7).
