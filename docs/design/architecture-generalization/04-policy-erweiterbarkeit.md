# 04 — Policy-Erweiterbarkeit: konfigurierbare Endpoints, sicher

Der Wunsch: Endpoints von außen hinzufügen können. Die Gefahr: die Endpoint-Tabelle *ist*
Policy an der Vertrauensgrenze. Zwei Röst-Runden haben den Entwurf hier zweimal umgeworfen:

- **Runde 1:** Der naive Ansatz („freie TOML-Zeile mit mindestens einem Scoping-Check") ist
  weder sicher noch anwenderfreundlich — der Scoping-Check kann das falsche Feld prüfen
  (Release: `tag_name` geprüft, `ref=main` erzeugt den Tag; `…/approve` als „note-artig"
  eingetragen hebelt Approval-Regeln aus). Antwort damals: Capability-Invarianten (§03.4)
  plus ein Pflicht-`capabilities`-Feld in der Nutzer-Zeile.
- **Runde 2:** Das nutzerdeklarierte `capabilities`-Feld ist für genau die neuen Endpoints
  Selbstbetrug. Der Capability-Layer trägt nur, wenn die Intent→Capability-Abbildung **Code**
  ist — für einen genuin neuen Nutzer-Endpoint gibt es diesen Code nicht, und die einzige
  Instanz zwischen Endpoint und `FORBIDDEN`-Menge wäre die vom Nutzer selbst getippte Zeile.
  Der Normalfall ist nicht Bosheit, sondern Unwissen („dass ein Release einen Tag erzeugt,
  wusste ich nicht") — exakt das Versehen, das der Layer eliminieren sollte.

Die Auflösung: **ein code-seitiger Endpoint-Katalog, aus dem die Config nur aktiviert.**
Das ist zugleich sicherer (keine Vertrauens-Verlagerung auf Nutzer-Deklarationen) und
anwenderfreundlicher („Endpoint aus geprüfter Liste einschalten" statt
„Sicherheits-DSL-Zeile erfinden").

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

## 04.2 Der Endpoint-Katalog: Code liefert, Config aktiviert

Der Guard liefert einen **Katalog** bekannter Forge-Endpoints als Code aus — deutlich mehr
Zeilen als heute aktiv sind. Jeder Katalog-Eintrag trägt, code-abgeleitet und golden-getestet:

- Methode + Pfad-Template,
- **Capabilities** (was der Call bewirken kann — vom Katalog-Autor im Code deklariert,
  nie vom Endnutzer; §03.4),
- Scoping-Checks aus der Registry (04.1) mit sinnvollen Defaults,
- Quoten-`kind` (M5), Regel-ID (A7),
- pro Entscheidungsfeld die Lage (Body vs. Query — wegen F12 muss das Forwarding dazu
  konsistent sein),
- seine **Deny-Sonden** für das Startgate (04.4).

Die Nutzer-Config **aktiviert** Einträge per ID und darf Parameter nur **verengen**:

```toml
# warden.toml — aktiviert Katalog-Einträge; erfindet nie eigene Zeilen
[api.endpoints]
enable = [
  "mr.create", "mr.note", "mr.discussion", "mr.discussion_reply",   # heutiger Default-Satz
  "mr.update", "pipeline.trigger",
  "release.create",            # zusätzlich gewünscht — Katalog kennt seine Capabilities
]

[api.endpoints.overrides."release.create"]
# nur Verengung erlaubt: engerer Präfix, kleinere Quote — nie zusätzliche Rechte
tag_prefix = "claude/release-"
```

- Ein Eintrag wie `release.create` deklariert im Code `capabilities={creates_tag}` und
  stirbt damit **beim Start** an der `FORBIDDEN`-Menge — es sei denn, der Katalog-Autor hat
  einen Scoping-Check hinterlegt, der die Capability nachweislich bändigt (z.B. `ref` muss
  Namespace-Präfix tragen ⇒ Tag entsteht nur auf eigenen Branches). Diese Abwägung trifft
  ein Mensch im Code-Review des Katalogs, nicht ein Endnutzer in einer Config-Datei.
- **Genuin neue Endpoints brauchen einen Katalog-PR.** Das ist die bewusste Grenze (A1/A2)
  — und sie ist ehrlich: schon Runde 1 räumte ein, dass Endpoints ohne Registry-Check und
  Capability-Abbildung einen Code-PR brauchen. Der Katalog macht diesen PR klein (eine
  Datenzeile + Golden-Test + Deny-Sonde) und gut reviewbar.
- **Alterung:** Der Katalog altert mit der Forge-API. Gegenmittel: er ist eine kompakte,
  deklarative Datenstruktur im Warden-Repo (Änderung = kleiner PR), und nicht-aktivierte
  Einträge kosten nichts. Das ist §6.9 („ageing safely") konsequent weitergedacht.

## 04.3 Fail-closed-Validierung beim Start (A9)

- Unbekannte Katalog-ID, Override, der einen Wert *erweitert* statt verengt, Kollision
  zwischen Overrides ⇒ `ConfigError`, Warden startet nicht.
- Die eingebauten Deny-Invarianten (Merge-Zeile, `FORBIDDEN`-Capabilities) stehen über
  allem und sind nicht deaktivierbar — auch nicht durch Nicht-Aktivieren-Tricks: sie
  matchen unabhängig vom Aktivierungszustand.
- Audit-Einträge nicht-default-aktivierter Einträge werden markiert
  (`rule = "gitlab.R3+enabled:release.create"`), im Viewer sofort sichtbar.
- `catraz doctor` druckt die **effektive** Tabelle (Default-Satz + Aktivierungen +
  Overrides) als eine Liste; `catraz allow-endpoint <id>` ist die CLI-Front dafür.

## 04.4 Policy-by-Example — Startgate und Vorschlagsgenerator

*Aus Röst-Runde 1 (Roaster-Idee 3), in Runde 2 auf den Katalog ausgerichtet: UX-Zucker über
dem Katalog, kein Sicherheitsmechanismus für unbekannte Endpoints.*

Nutzer drücken Absicht als **beobachtbares Verhalten** aus — Beispiel-Assertions in
`.catraz/policy-tests/`:

```toml
[[must_allow]]
description = "Release auf eigenem Branch anlegen"
method = "POST"
path   = "/projects/mygroup/myproj/releases"
body   = { tag_name = "claude/release-v1", ref = "claude/feature-x" }

[[must_deny]]
description = "Tag auf main via Release"
method = "POST"
path   = "/projects/mygroup/myproj/releases"
body   = { tag_name = "claude/release-v1", ref = "main" }
```

- Der Warden startet nur, wenn die effektive Policy alle Assertions erfüllt — die Beispiele
  sind ein **Startgate**, kein Mechanismus. Daten können nichts öffnen, was der Code nicht
  ohnehin erlaubt (A2).
- **Eigentümerschaft der Deny-Sonden (Runde 2):** Der kuratierte `must_deny`-Satz lebt
  **beim Katalog** — jeder Katalog-Eintrag bringt seine Sonden mit und sie laufen
  automatisch für jeden aktivierten Eintrag. Damit gibt es einen Korpus mit einem
  Eigentümer (Katalog-Autor + Review) statt eines herrenlosen Seed-Verzeichnisses, und die
  Sonden existieren genau dann, wenn der Endpoint existiert.
- `catraz allow-endpoint --from-example` kann aus einem `must_allow`-Beispiel den passenden
  Katalog-Eintrag (plus minimale Verengungs-Overrides) **vorschlagen** — der Nutzer
  bestätigt eine konkrete, lesbare Aktivierung statt sie zu erfinden.

## 04.5 Richtung für self-hosted: kurzlebige, eng gescopte Credentials

*Aus Röst-Runde 1 (Roaster-Idee 4), als Option dokumentiert.*

Heute hält der Warden ein langlebiges `api`-Scope-PAT und rekonstruiert Ownership per
`GET /user` + Autor-Vergleich — fragil (der Fine-grained-PAT/`User:Read`-Footgun im README
beweist es). Wo die Plattform es hergibt (GitLab self-hosted: Project Access Tokens,
Impersonation), kann der Guard stattdessen **kurzlebige, projekt-gescopte Tokens** minten
und den nativen Layer mehr tragen lassen (A10) — weniger Ownership-Parserei an der
Vertrauensgrenze; ein projekt-gescoptes **Read**-Token entschärft zusätzlich B1 nativ.
Auf gitlab.com nicht allgemein machbar (Rollen-/Admin-Anforderungen), daher: dokumentierte
Deployment-Option, keine Grundlage der Architektur.
