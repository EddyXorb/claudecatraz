# 06 — Migrationspfad und Anti-Ziele

## 06.1 Migrationspfad — kein Big Bang

Reihenfolge nach zwei Röst-Runden: **Sicherheitsbefunde zuerst** (sie sind unabhängig von
jeder Abstraktion), dann Schema-Versionierung (Voraussetzung für alles, was Namen ändert),
dann Nutzerwert und Refactorings. Jeder Schritt ist einzeln shipbar; die bestehenden Tests
sind das Verhaltens-Netz. Jeder Schritt synchronisiert `docs/design/agentic-workflow/`
(die kanonische Design-Doku, deren W-/R-Referenzen im Code stehen) mit.

1. **B1 fixen: Read-Pfad scopen** — Schutzlinie „Inhalt, nicht Sichtbarkeit"
   (Maintainer-Entscheid): als **minimale Read-Tabelle** gebaut mit vier Kategorien
   (projekt-gebunden → Gate wie heute; projektlose Metadaten → pass; projektlose
   inhaltsfähige Endpoints wie globale/Gruppen-Blob- und Commit-Suche und `/snippets` →
   deny; unbekannt → default-deny), die Schritt 4 nur erweitert, nicht ersetzt (kein
   Rework, Röst-Runde 2). Beifang: F12 für den `scope`-Query-Parameter (Query konsistent
   in Entscheidung **und** Forwarding) und B5: `/api/graphql` explizit 403 + Audit.
   Red-Team-Tests dazu (`GET /search?scope=blobs` muss 403 sein;
   `GET /groups/<id>/projects` muss funktionieren; GraphQL muss 403 sein).
2. **Regel-Registry + Schema-Versionierung** — zentrale Regel-IDs inkl. reserviertem
   Kernel-Namespace `core.*` (fixt B3, Röst-R7), `schema_version` in Audit-JSONL und
   State-DB, Kompat-Fenster für Viewer/`observe`. Voraussetzung für claude→agent- und
   channel→guard-Renames (F11).
3. **Capability-Invarianten-Ebene** (§03.4) — fixt B2 kanalübergreifend für alle
   code-bekannten Endpoints; klein, pur, golden-getestet. Voraussetzung für Schritt 4.
4. **Endpoint-Katalog + Check-Registry + Aktivierungs-Config** (§04.1–04.3) — der
   nutzersichtbare Gewinn. Jeder Katalog-Eintrag bringt seine Deny-Sonden mit (§04.4), und
   das Startgate, das sie ausführt, ist **Teil dieses Schritts** — nicht ein späterer
   (Röst-Runde 2: sonst shipped Schritt 4 unabgesichert). Fixt F2/F10/F12 nebenbei.
5. **Kernel-Extraktion + Intent-Split** (§03.2/03.3, inkl. `intent.writes` für das
   read-only-Gate) — reines Refactoring, von `test_api_proxy`/`test_git_proxy` abgedeckt;
   fixt F1/F3/F6; blockiert keinen Nutzerwert und wird deshalb *nicht* vorgezogen.
6. **Rename claude→agent im Warden** — jetzt trivial, weil Schritt 2 die Migrationen
   bereitstellt (State-Tabellen `claude_branches`/`claude_mrs`, Audit-Felder).
7. **Agent-Layer: Entrypoint-Zerlegung** (§05.2) — generischer Entrypoint + Claude-Adapter
   (inkl. `environ`/`render_instructions`), verhaltenserhaltend, von `tests/container/`
   abgedeckt. Danach Manifest + Profile (§05.3), `catraz`-CLI-Entkopplung und die
   **Adapter-Conformance-Harness** (§05.5) — sie ist die Abnahme dieses Schritts.
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
