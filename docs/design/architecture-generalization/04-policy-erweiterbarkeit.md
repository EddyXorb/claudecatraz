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

## 04.5 Umsetzungsnotizen (Schritt 4, tatsächlicher Code)

*Ergänzt nach der Umsetzung — wo der Code bewusst von der obigen Skizze abweicht, und warum.*

- **Audit-Markierung als eigenes Feld, nicht als `rule`-Suffix.** §04.3 skizzierte
  `rule = "gitlab.R3+enabled:release.create"`. Umgesetzt ist stattdessen ein additives
  Audit-Feld `enabled_via` (`"config:<id>"`, nur gesetzt für nicht-default-aktivierte
  Einträge; ganz abwesend für den Default-Satz) — die `rule`-ID bleibt unverändert die
  reine Registry-ID aus `rules.py`. Grund: die Regel-Registry (§06 Schritt 2) hat genau
  das Gegenteil-Problem gelöst — Regel-IDs als Streuliteral/verschmutzte Strings (B3). Ein
  zusammengesetzter `rule`-String hätte diese Disziplin sofort wieder aufgeweicht (jeder
  Leser von `rules.RULES` hätte ab dann auch `+enabled:`-Suffixe parsen müssen). Ein
  eigenes Feld ist zusätzlich strukturiert abfragbar (Viewer/`catraz doctor`), statt aus
  einem String zurückgeparst werden zu müssen. In die Audit-Feld-Allowlist aufgenommen
  (`audit._ALLOWED_FIELDS`); **kein** `AUDIT_SCHEMA_VERSION`-Bump — anders als der
  R2→R4-Rename (Schritt 2, Version 2), der eine *bestehende* Feldbedeutung änderte, ist
  `enabled_via` ein komplett neues, standardmäßig abwesendes Feld. Genau diese Additivität
  ist es, die O.5s Feld-Allowlist-Redaction von Anfang an zulassen sollte: jeder Leser
  (Viewer, `catraz observe`) rendert unbekannte/fehlende Felder bereits defensiv.
- **Kein Taming-Mechanismus für FORBIDDEN-Capabilities (YAGNI).** §04.2 skizzierte einen
  Scoping-Check, der eine FORBIDDEN-Capability nachweislich bändigt (das `release.create`-
  Beispiel: `ref` muss Namespace-Präfix tragen ⇒ der erzeugte Tag liegt nur auf eigenen
  Branches). Dieser Schritt implementiert das **nicht**: ein Katalog-Eintrag, dessen
  statische `capabilities` die `FORBIDDEN`-Menge schneiden, kann grundsätzlich nicht
  aktiviert werden — `activation.build_effective_table` bricht mit `CatalogConfigError`
  (→ `ConfigError`) ab, sobald ein solcher Eintrag in `enable` steht, ganz ohne
  Umgehungsmöglichkeit. Das ist eine bewusste Auslassung: kein aktueller Katalog-Eintrag
  (auch nicht `branch.create`/`issue.create`) braucht sie, und ein Taming-Mechanismus ohne
  einen echten Verbraucher wäre spekulative Generalität (§06.2-Anti-Ziel „kein generisches
  Proxy-Framework als Selbstzweck"). Der richtige Zeitpunkt dafür ist der erste reale
  Katalog-Eintrag, der eine FORBIDDEN-Capability trägt (der Kandidat aus der Skizze bleibt
  `release.create` — `creates_tag`) — dann bekommt `OverridableParam`/`activation.py` eine
  zusätzliche Validierungsstufe „ist die Capability durch einen der Checks nachweislich
  gebändigt", nicht vorher.
- **Der Override-Mechanismus existiert, aber kein Default-Eintrag braucht ihn.**
  `CatalogEntry.overridable` (Liste von `OverridableParam`: TOML-Schlüssel, welcher Check
  ersetzt wird, ein `is_narrower`-Beweis, ein `rebuild`) ist generisch, aber nur
  `branch.create` deklariert einen Knopf (`branch_prefix`, per
  `Config.in_branch_namespace` als Verengungs-Beweis — ein neuer literaler Präfix muss
  selbst innerhalb des allgemeinen Namensraums liegen). Keiner der sechs Default-Einträge
  braucht das: `field_has_prefix` prüft dort gegen `Config.branch_prefixes` (den
  deployment-weiten Namensraum), nicht gegen einen im Katalog fest verdrahteten String —
  es gibt nichts Literales, das ein Override sinnvoll verengen könnte. `branch.create`
  demonstriert den Mechanismus end-to-end (golden- und aktivierungs-getestet), damit er
  beim ersten echten Bedarf (z. B. `release.create`s `tag_prefix`) bereits erprobt ist.
- **F12-Fix ist auf den Schreibpfad/Katalog begrenzt.** `read_endpoints.py` (Schritt 1)
  hatte sein eigenes F12-Beifang bereits gelöst (Query wird konsistent in Entscheidung und
  Forwarding verwendet) und wird von diesem Schritt unverändert weiterverwendet. Der neue
  Fix betrifft nur den *Schreib*pfad: `CatalogEntry.decision_fields` deklariert pro Feld
  Body oder Query, und `api_proxy._extract_fields` liest für einen gematchten
  Katalog-Eintrag ausschließlich diese deklarierten Felder aus der deklarierten Lage —
  kein Merge von Query- und Body-Feldern mehr für Writes.
- **`Config.effective_endpoints` als memoisierte `cached_property`, nicht als expliziter
  Funktionsparameter.** `policy.decide(req, state, cfg)` behält seine Signatur (kein
  Aufruf-Update nötig in `test_policy.py`/`test_quota.py`/`test_rules.py`, die alle direkt
  gegen `decide` testen) — `_decide_api` liest `cfg.effective_endpoints` selbst. Das
  Startgate (`startgate._probe_config`) seedet diesen Cache direkt (dieselbe
  `instance.__dict__`-Mechanik, die `functools.cached_property` selbst benutzt), damit es
  exakt die ihm übergebene Tabelle validiert — auch eine testweise von Hand gebaute, die zu
  keiner realen `[api.endpoints]`-Config gehört — statt eine möglicherweise abweichende
  Tabelle aus `cfg.endpoint_activation` neu zu bauen.

## 04.6 Richtung für self-hosted: kurzlebige, eng gescopte Credentials

*Aus Röst-Runde 1 (Roaster-Idee 4), als Option dokumentiert.*

Heute hält der Warden ein langlebiges `api`-Scope-PAT und rekonstruiert Ownership per
`GET /user` + Autor-Vergleich — fragil (der Fine-grained-PAT/`User:Read`-Footgun im README
beweist es). Wo die Plattform es hergibt (GitLab self-hosted: Project Access Tokens,
Impersonation), kann der Guard stattdessen **kurzlebige, projekt-gescopte Tokens** minten
und den nativen Layer mehr tragen lassen (A10) — weniger Ownership-Parserei an der
Vertrauensgrenze; ein projekt-gescoptes **Read**-Token entschärft zusätzlich B1 nativ.
Auf gitlab.com nicht allgemein machbar (Rollen-/Admin-Anforderungen), daher: dokumentierte
Deployment-Option, keine Grundlage der Architektur.
