# 06 — Migrationspfad und Anti-Ziele

## 06.1 Migrationspfad — kein Big Bang

Reihenfolge nach Röst-Runde 1 umgebaut: **Sicherheitsbefunde zuerst** (sie sind unabhängig
von jeder Abstraktion), dann Schema-Versionierung (Voraussetzung für alles, was Namen
ändert), dann erst Nutzerwert und Refactorings. Jeder Schritt ist einzeln shipbar; die
bestehenden Tests sind das Verhaltens-Netz. Jeder Schritt synchronisiert
`docs/design/agentic-workflow/` (die kanonische Design-Doku, deren W-/R-Referenzen im Code
stehen) mit.

1. **B1 fixen: Read-Pfad scopen** — projektlose Reads default-deny + kleine eingebaute
   Read-Allowlist (`/user`, `/version`, …). Reiner Sicherheitsgewinn, keine Abstraktion
   nötig, vorhandene Struktur reicht. Red-Team-Test dazu (`GET /projects` muss 403 sein).
2. **Regel-Registry + Schema-Versionierung** — zentrale Regel-IDs (fixt B3), `schema_version`
   in Audit-JSONL und State-DB, Kompat-Fenster für Viewer/`observe`. Voraussetzung für
   claude→agent- und channel→guard-Renames (F11).
3. **Capability-Invarianten-Ebene** (§03.4) — fixt B2 kanalübergreifend; klein, pur,
   golden-getestet. Voraussetzung für Schritt 4.
4. **Check-Registry + TOML-Endpoints + Read-Tabelle** (§04.1–04.3) — der nutzersichtbare
   Gewinn, jetzt gefahrlos. Fixt F2/F10 nebenbei.
5. **Kernel-Extraktion + Intent-Split** (§03.2/03.3) — reines Refactoring, von
   `test_api_proxy`/`test_git_proxy` abgedeckt; fixt F1/F3/F6; blockiert keinen Nutzerwert
   und wird deshalb *nicht* vorgezogen.
6. **Rename claude→agent im Warden** — jetzt trivial, weil Schritt 2 die Migrationen
   bereitstellt (State-Tabellen `claude_branches`/`claude_mrs`, Audit-Felder).
7. **Agent-Layer: Entrypoint-Zerlegung** (§05.2) — generischer Entrypoint + Claude-Adapter,
   verhaltenserhaltend, von `tests/container/` abgedeckt. Danach erst Manifest + Profile
   (§05.3) und `catraz`-CLI-Entkopplung.
8. **Policy-by-Example** (§04.4) — Startgate-Assertions + Vorschlagsgenerator; baut auf 3+4.
9. **Zweiter Guard** (Forge oder DB, §03.5/03.7) — die Belastungsprobe der Abstraktion,
   zusammen mit der Prozess-Trennung pro credential-haltendem Guard (§03.6). Bewusst zuletzt:
   erst generalisieren, wenn der zweite Anwender existiert.

Abhängigkeiten kompakt: 1 ist frei; 2 → 6; 3 → 4 → 8; 5 ist frei (nach 3 sinnvoll);
7 ist frei; 9 braucht 5.

## 06.2 Was man bewusst NICHT tun sollte (Anti-Ziele)

- **Kein dynamisches Plugin-Laden** (entry_points, `importlib` aus Config-/`.catraz`-Pfaden):
  Config darf nie zu Code-Ausführung führen (A2). Guards und Agent-Adapter sind einkompiliert
  bzw. mitgeliefert; Config wählt und parametrisiert.
- **Keine Wildcard-/Regex-Policy-DSL** in Nutzer-Config — A8 gilt auch für neue
  Erweiterungspunkte. Zehn explizite Zeilen schlagen ein cleveres Muster.
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
