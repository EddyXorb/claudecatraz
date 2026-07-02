# 04 — Policy-Erweiterbarkeit: konfigurierbare Endpoints, sicher

Der Wunsch: Endpoints von außen hinzufügen können. Die Gefahr: die Endpoint-Tabelle *ist*
Policy an der Vertrauensgrenze. Röst-Runde 1 hat gezeigt, dass der naive Ansatz („TOML-Zeile
mit mindestens einem Scoping-Check") **weder sicher noch anwenderfreundlich** ist — der
Scoping-Check kann schlicht das falsche Feld prüfen (Release: `tag_name` geprüft, `ref=main`
erzeugt den Tag; `…/approve` als „note-artig" eingetragen hebelt Approval-Regeln aus).
Deshalb ist die Reihenfolge zwingend: **erst Capability-Invarianten (§03.4), dann
Konfigurierbarkeit.**

## 04.1 Check-Registry (Code, A2)

Die Prädikate werden zu benannten, parametrisierbaren Bausteinen in einer Registry:

```python
CHECKS = {
    "field_has_prefix":   …,   # vereinigt src_branch_prefix/ref_prefix (heute Duplikate, F10)
    "owned_by_agent":     …,   # heutiges mr_owned_by_claude; deklariert needs={"mr_owner"}
    "field_not_equals":   …,   # verallgemeinert not_merge_intent
}
```

Jeder Check deklariert seine **Datenbedürfnisse** (`needs = {"mr_owner"}`), die der Kernel in
der `enrich`-Phase auflöst — das ersetzt die Identitätsprüfung
`mr_owned_by_claude in ep.checks` (`api_proxy.py:102`, F2).

## 04.2 Tabelle aus zwei Quellen — mit Capability-Pflicht

1. **Eingebaute Zeilen (Code):** die heutige Write-Tabelle plus die neue Read-Tabelle (B1),
   inklusive aller Deny-Invarianten. Invarianten (Capability-Ebene + Merge-Zeile) werden
   **immer zuerst** ausgewertet und sind von Config-Zeilen nicht verschattbar (A2).
2. **Nutzer-Zeilen (`warden.toml`):**

```toml
[[api.write_endpoints]]
method       = "POST"
path         = "/projects/{id}/releases"
kind         = "note"                       # Quoten-Dimension (M5)
capabilities = ["creates_tag"]              # PFLICHT: was der Call bewirken KANN
checks = [
  { check = "field_has_prefix", field = "tag_name" },
]
```

Das `capabilities`-Feld ist der Unterschied zwischen Loaded Gun und brauchbarem Feature:
Der Nutzer muss deklarieren, was der Endpoint bewirken kann; die Deklaration wird gegen die
einkompilierte `FORBIDDEN`-Menge geprüft. Dieses Beispiel **stirbt beim Start** mit
„`creates_tag` ist verboten (M4)" — der Nutzer erfährt es sofort, nicht der Auditor drei
Wochen später. Falschdeklaration (Capability weggelassen) bleibt möglich, ist aber (a) eine
bewusste Lüge statt eines Versehens, (b) grep-bar und review-bar, und (c) für die
eingebauten und kuratierten Zeilen durch Golden-Tests abgedeckt.

## 04.3 Fail-closed-Validierung beim Start (A9)

- Unbekannter Check-Name, unbekanntes `kind`, unbekannte Capability, fehlende
  `capabilities`-Deklaration, Pfad-Kollision mit einer eingebauten Zeile ⇒ `ConfigError`,
  Warden startet nicht.
- Jede Nutzer-Zeile braucht mindestens einen scoping Check (Namespace M2 oder Ownership M3);
  ungescopte Zeilen nur mit explizitem, hässlichem `unscoped_i_know_what_i_am_doing = true`
  (grep-bar, A8).
- Audit-Einträge aus Nutzer-Zeilen werden markiert (`rule = "gitlab.R3+user"`), im Viewer
  sofort sichtbar.
- `catraz doctor` druckt die **effektive** Tabelle (eingebaut + Nutzer) als eine Liste.

## 04.4 Policy-by-Example — die anwenderfreundliche Oberfläche darüber

*Übernommen aus Röst-Runde 1 (Roaster-Idee 3), als Ergänzung, nicht Ersatz.*

Nutzer, die keine Endpoint-Zeilen schreiben wollen (die Mehrheit), drücken Absicht als
**beobachtbares Verhalten** aus — Beispiel-Assertions in `.catraz/policy-tests/`:

```toml
[[must_allow]]
description = "Release-Notes auf eigenem Branch anlegen"
method = "POST"
path   = "/projects/mygroup/myproj/releases"
body   = { tag_name = "claude/v1", ref = "claude/feature-x" }

[[must_deny]]
description = "Tag auf main via Release"
method = "POST"
path   = "/projects/mygroup/myproj/releases"
body   = { tag_name = "claude/v1", ref = "main" }
```

- Der Warden startet nur, wenn die effektive Policy alle Assertions erfüllt — die Beispiele
  sind ein **Startgate**, kein Mechanismus. Daten können nichts öffnen, was der Code nicht
  ohnehin erlaubt (A2 bleibt sauber).
- `catraz allow-endpoint --from-example` kann aus einem `must_allow`-Beispiel die minimal
  nötige Tabellenzeile **vorschlagen** und zur Review anzeigen — der Nutzer bestätigt eine
  konkrete, lesbare Zeile statt sie zu erfinden.
- Die Fehlbedienung „falsches Feld geprüft" wird von der `must_deny`-Seite gefangen: wer das
  Tag-auf-main-Beispiel als Deny-Assertion dabei hat (kuratierter Standard-Satz, den `init`
  seedet), kann die Lücke nicht unbemerkt öffnen.

**Grenze (ehrlich):** Ein genuin neuer Endpoint, für den es keinen Registry-Check und keine
Capability-Abbildung gibt, braucht weiterhin einen Code-PR. Das ist korrekt so (A1/A2) —
Erweiterbarkeit endet da, wo ungeprüfte Mechanismen anfangen würden.

## 04.5 Richtung für self-hosted: kurzlebige, eng gescopte Credentials

*Aus Röst-Runde 1 (Roaster-Idee 4), als Option dokumentiert.*

Heute hält der Warden ein langlebiges `api`-Scope-PAT und rekonstruiert Ownership per
`GET /user` + Autor-Vergleich — fragil (der Fine-grained-PAT/`User:Read`-Footgun im README
beweist es). Wo die Plattform es hergibt (GitLab self-hosted: Project Access Tokens,
Impersonation), kann der Guard stattdessen **kurzlebige, projekt-gescopte Tokens** minten
und den nativen Layer mehr tragen lassen (A10) — weniger Ownership-Parserei an der
Vertrauensgrenze. Auf gitlab.com nicht allgemein machbar (Rollen-/Admin-Anforderungen),
daher: dokumentierte Deployment-Option, keine Grundlage der Architektur.
