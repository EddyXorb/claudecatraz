# 09 — Endpoint-Actions: per-Endpoint erlaubte Agent-Aktionen

Bisher ist per-Endpoint nur konfigurierbar, *wo* der Agent arbeiten darf
(`allowed_projects`) und *wie streng* die Limits sind (`rules`); *was* er tun darf,
ist global: die REST-Write-Aktivierung (`[api.endpoints].enable`) gilt für alle
Hosts gleich, und der git-Transport (fetch/push) wird nur indirekt über die
Token-Präsenz (§4.2 in 08) gesteuert. Dieses Dokument führt **`actions`** ein — eine
Liste erlaubter Agent-Aktionen mit Domänen-Default und per-Endpoint-Override — und
absorbiert `[api.endpoints]` ersatzlos.

Voraussetzung: 08-multi-target vollständig umgesetzt (insbesondere Endpoint-Taxonomie
§3, per-Host-Routing §1/§2 und State-Keying §5). Dieser Schritt kommt **nach** 08;
er wird nicht in dessen laufende Schritte gemischt.

> **Für Menschen vs. für Agents.** Wie bei 08 beschreibt dieses Dokument das *Was*
> und *Warum*. Die schrittweise Umsetzung (Unterordner `09-endpoint-actions/`) wird
> erst abgeleitet, wenn 08 abgeschlossen ist.

---

## 1. Die Sprache: `actions`

### 1.1 Eine Liste, geschlossenes `noun.verb`-Vokabular

`actions` ist **eine** Liste von IDs im bestehenden Katalog-Stil (`mr.create`,
`pipeline.trigger`, …), erweitert um die git-Transport-Verben:

```toml
[git]
actions = ["git.fetch", "git.push",
           "mr.create", "mr.comment", "mr.update",
           "pipeline.trigger"]

[git.rules]
branch_prefixes     = ["claude/"]
max_push_bytes      = "50MiB"
max_open_branches   = 10
max_open_mrs        = 5
max_writes_per_hour = 60

[[git.endpoint]]
host             = "gitlab.com"
type             = "gitlab"
allowed_projects = ["acme/infra"]
# kein actions-Key → Domänen-Default gilt

[[git.endpoint]]
host             = "my-gitlab.de"
type             = "gitlab"
allowed_projects = ["acme/app"]
actions          = ["git.fetch", "mr.comment"]      # reiner Review-Endpoint
rules            = { max_writes_per_hour = 30 }

[[git.endpoint]]
host             = "personal-gitserver.it"
type             = "plain"
allowed_projects = ["me/dotfiles"]
# erbt [git].actions, geschnitten mit type="plain" → {git.fetch, git.push} (§3.2)
```

Bewusst **nicht** gewählte Formen (Begründungen in §7):

- **kein** Read/Write-Split (`read_actions`/`write_actions`) — die Achse existiert
  schon zweimal (Token-abgeleiteter Access-Mode §4.2 in 08; Methode/`kind` des
  Recognizers) und würde hier ein drittes, widerspruchsfähiges Mal kodiert.
- **keine** Namenskonvention (`write_push`, `read_pull`) — der Warden *weiß* aus dem
  Recognizer, ob eine ID schreibt; ein per String-Konvention geparster Präfix ist
  genau der offene Vertrag, den das geschlossene Vokabular vermeidet.
- **keine** Wildcards (`mr.*`) — ein später ergänzter Katalog-Eintrag würde sonst in
  bestehenden Deployments *stillschweigend* aktiv; neue Aktivierungen sind bewusste
  Edits (dieselbe Doktrin wie die Read-Tabelle).

### 1.2 Action ≠ Recognizer: Gruppierung in Code

Eine Action ist eine **in Code definierte, geschlossene Menge von Recognizern bzw.
Transport-Operationen** — Agent-Granularität, nicht Wire-Granularität. Wer
`mr.note` aktivierte und `mr.discussion_reply` vergäße, hätte einen Agenten, der
Review-Threads nicht beantworten kann; die Gruppierung macht diesen Fußangel
unmöglich. Die Abbildung ist nicht konfigurierbar und lebt neben dem Katalog:

| Action | deckt ab | Art | Default |
| --- | --- | --- | --- |
| `git.fetch` | advertise(upload) + upload-pack | git-Transport, read | ✔ |
| `git.push` | advertise(receive) + receive-pack | git-Transport, write | ✔ |
| `mr.create` | Recognizer `mr.create` | REST write | ✔ |
| `mr.comment` | `mr.note`, `mr.discussion`, `mr.discussion_reply` | REST write | ✔ |
| `mr.update` | Recognizer `mr.update` | REST write | ✔ |
| `pipeline.trigger` | Recognizer `pipeline.trigger` | REST write | ✔ |
| `branch.create` | Recognizer `branch.create` | REST write | ✖ |
| `issue.create` | Recognizer `issue.create` | REST write | ✖ |

Der Built-in-Default (rechte Spalte) entspricht dem heutigen `DEFAULT_ENABLED` plus
den beiden Transport-Verben — ein `warden.toml` ohne jeden `actions`-Key verhält
sich exakt wie heute.

**Granularitätsgrenze:** eine Action kann nicht feiner sein als ein Recognizer.
`mr.update` deckt Titel-Edit *und* `state_event=close`; feldabhängige
Unterscheidungen (z.B. `state_event=merge`) bleiben Sache der Capability-Schicht
(`api_capabilities`), nie der Config-Sprache.

### 1.3 Platzierung: Scope-Achse neben `rules`, nicht darin

`actions` liegt als nackter Listen-Key unter `[git]` (Domänen-Default) und direkt im
Endpoint-Entry (Override) — **nicht** in `rules`. Das erweitert die WHAT-vs-HOW-Linie
aus 08 §3.1 um eine Kategorie:

> **Identität** (`host`, `type`) und **host-relativer Scope** (`allowed_projects`)
> kaskadieren nicht; **host-unabhängiger Scope** (`actions`) und **Verhalten**
> (`rules`) kaskadieren — per-Key-Merge, Listen ersetzen komplett.

Gründe gegen einen `rules`-Key `actions`:

- `rules` bleibt homogen: lauter Drehregler (Limits, Prefixes), die immer laufende
  Checks parametrisieren. `actions` ist der **Schalter davor** — es entscheidet,
  welche Checks es überhaupt gibt. Vier von fünf Regel-Keys sind tot, wenn die
  zugehörige Action fehlt (`max_open_mrs` ohne `mr.create`, `branch_prefixes` /
  `max_push_bytes` / `max_open_branches` ohne `git.push`); keiner wirkt umgekehrt
  auf `actions`. Diese Hierarchie wäre in einem flachen Namespace unsichtbar.
- **Diff-Review:** ein Diff auf `actions` ist eine Änderung der Sicherheitsfläche,
  ein Diff auf `rules` ist Tuning. Getrennte Keys machen das am Diff-Ort ablesbar,
  statt es im Inline-Table verstecken zu können.
- **Validierung:** `actions` hat ein geschlossenes, `type`-abhängiges Vokabular
  (§3.2); `rules`-Keys sind type-unabhängig. Getrennte Keys, getrennte Regeln.

**Syntax-Festlegungen** (TOML-1.0-Realitäten, `tomllib`):

- `actions` ist ein Listen-Key — eine Header-Form `[git.actions]` ist unmöglich
  (Header deklarieren Tabellen) und die Ausweich-Formen (Wrapper-Tabelle
  `enable = [...]`, Boolean-Map) sind schlechter: die Boolean-Map zerstört die
  „Liste ersetzt komplett"-Semantik (§1.4). Als Key ist `actions` auf beiden Ebenen
  syntaktisch identisch — symmetrischer als jede Header-Variante.
- `rules` bleibt wie in 08: Header `[git.rules]` an der Wurzel (viele Keys, ein
  Inline-Table wäre eine unlesbare Ein-Zeilen-Wurst — TOML-1.0-Inline-Tables sind
  einzeilig), Inline-Map am Endpoint (wenige Keys). Der Parser behandelt beide
  Schreibweisen ohnehin identisch; das Schema erzwingt hier nichts.
- Die Schreibweise `[git.endpoint.rules]` (Header nach einem `[[git.endpoint]]`)
  ist valide, wird aber im Template **nicht** verwendet und in der Doku abgeraten:
  der Header bindet an das *positionell letzte* Array-Element — wer Endpoint-Blöcke
  umsortiert und den Header vergisst, hängt Regeln kommentarlos an den falschen
  Host. Valides TOML, für Loader und `doctor` unerkennbar. Die Inline-Map ist gegen
  Umsortieren immun.

### 1.4 Kaskade

Identisch zur Regel-Kaskade (08 §3.3): Effektivwert = `endpoint.actions` falls
gesetzt, sonst `[git].actions`, sonst Built-in-Default (§1.2). Die Liste **ersetzt
komplett** — nur so kann ein Override verengen. Es gibt kein `actions_add`/
`actions_remove`; wer „Default plus eins" will, wiederholt die Liste (Konsistenz mit
`branch_prefixes` schlägt Bequemlichkeit; nachrüstbar bei echtem Schmerz).

## 2. Schichtung: Token = *kann*, Actions = *darf*, Capabilities = *nie*

`actions` verengt nur innerhalb dessen, was die härteren Schichten ohnehin zulassen:

1. **Capability-Invarianten** (`core.capabilities.FORBIDDEN`) sind compiled-in und
   bleiben unkonfigurierbar. Die Sprache kann per Konstruktion nie „merge erlauben"
   ausdrücken: der bestehende FORBIDDEN-Check beim Tabellenbau bleibt als Backstop,
   der Built-in-Merge-Deny (`catalog/builtin.py`) bleibt außerhalb jeder Tabelle.
2. **Access-Mode** (Token-Präsenz, 08 §4.2) ist die harte Decke: ohne write_token
   sind Write-Actions wirkungslos konfiguriert (Endpoint ist faktisch read-only) —
   kein Fehler, aber eine `doctor`-Warnung (§4).
3. **`actions`** wählt darunter aus, was der Agent tatsächlich benutzen darf.
4. **Die REST-Read-Tabelle bleibt invariant.** `read_endpoints.py` ist bewusst
   nicht action-adressierbar: eine Config-Sprache, die Content-Exposure-Zeilen
   umschalten könnte, wäre genau die Konfigurierbarkeit, gegen die FORBIDDEN gebaut
   wurde. REST-Reads werden weiterhin von Read-Tabelle + Projekt-Allowlist regiert;
   `git.fetch` gated nur den git-Transport-Read.

## 3. Validierung (fail-closed, 08 §3.4 erweitert)

### 3.1 Struktur

- Unbekannte Action-ID (Tippfehler) → `ConfigError`, Start bricht ab.
- `actions` ist kein Array von Strings → `ConfigError`.

### 3.2 `type`-Schnitt: geerbter Überschuss wird gefiltert, expliziter Widerspruch ist ein Fehler

Actions haben `type`-abhängige Gültigkeit: `mr.*`/`pipeline.*`/`issue.*` existieren
auf `type = "plain"` nicht. Zwei Fälle, zwei Verhalten:

- **Geerbter Domänen-Default** darf Forge-Actions enthalten; ein `plain`-Endpoint
  erbt davon den Schnitt mit seinem Typ (→ `{git.fetch, git.push}`). Alles andere
  würde erzwingen, dass jedes gemischte Deployment jeden plain-Endpoint überschreibt.
- **Explizites `actions` am Endpoint** mit einer für den Typ unmöglichen ID
  (`mr.create` auf `plain`) → `ConfigError` — das ist immer ein Irrtum, derselbe
  Typo-Schutz wie bei unbekannten `rules`-Keys.

## 4. Wechselwirkungen & `doctor`

Kohärenzprobleme zwischen Actions sind **keine** Sicherheitsprobleme — der Warden
failt nicht, `doctor` warnt (freundlich/erklärend, wie in 08 §6):

- Write-Actions konfiguriert, aber kein write_token für den Host → Endpoint faktisch
  read-only; Warnung nennt den Fix.
- `mr.create` ohne `git.push` → der Source-Branch kann nie entstehen; Warnung.
- `pipeline.trigger` ohne `git.push` → analog; Warnung.
- Tote Quotas (`max_open_mrs` gesetzt, `mr.create` fehlt) sind harmlos und keine
  Warnung wert — der Zähler wird schlicht nie erreicht.

**Kein Laufzeit-Reload:** die effektiven Tabellen werden einmalig beim Start gebaut
(dieselbe Doktrin wie `build_effective_table` heute — kein Rebuild, kein Drift).
Reconcile läuft unabhängig von `actions` weiter (nur GETs); eine per Neustart
entzogene Action lässt existierende Branches/MRs unberührt, sie sind nur nicht mehr
erweiterbar.

## 5. Implementierungsskizze

- **Config:** `GitEndpoint` bekommt `actions: Optional[tuple[str, ...]] = None`,
  `Config` ein Domänen-`git_actions`; `effective_actions(host)` kaskadiert mit
  derselben `_cascade`-Hilfsfunktion wie `effective_rules` und schneidet mit dem
  Endpoint-`type` (§3.2). Absent (`None`) und explizit leer (`[]` = Endpoint kann
  nichts) bleiben unterscheidbar.
- **Action-Katalog:** neues Modul neben dem Katalog (z.B.
  `guards/gitlab_api/catalog/actions.py` für die Forge-Seite plus die
  Transport-Verben im git-Guard): Abbildung Action-ID → Recognizer-IDs bzw.
  git-Operationen, dazu der Built-in-Default. Geschlossen, in Code, getestet.
- **REST-Guard:** `build_effective_table` läuft **pro Endpoint**; der `ApiGuard`
  hält `host → EffectiveTable` statt einer globalen Tabelle (billig: N Tabellen,
  einmalig beim Start; `intent.host` ist seit 08-Schritt-03 überall verfügbar).
  `enabled_via` und der `/policy`-Report werden per-Host.
- **git-Guard:** kleiner Gate analog `host_gate`: Operation → Action-ID
  (`advertise(upload)`/`upload-pack` → `git.fetch`; `advertise(receive)`/
  `receive-pack` → `git.push`), Deny wenn nicht in den effektiven Actions des
  Hosts. Der Deny bereits bei `advertise` gibt dem git-Client eine saubere
  Fehlermeldung, bevor er den Pack schickt — dieselbe Form wie der `_writes`-Pfad.
- **`[api.endpoints]` entfällt ersatzlos** (eine Einstellung, eine Quelle — analog
  08 §3.5; pre-1.0, keine Rückwärtskompatibilität). `ApiEndpointsConfig`/
  `parse_api_endpoints` werden entfernt, `build_effective_table` konsumiert die
  effektiven Actions.
- **Template/`init`:** das gescaffoldete `warden.toml` setzt den Default explizit
  als `[git] actions = [...]` und dokumentiert das Vokabular (Tabelle §1.2) als
  Kommentar. Der Built-in-Default existiert **zusätzlich** im Code — fehlender Key
  ≠ leere Liste.
- **`doctor`:** die Kreuz-Checks aus §4, pro Host.

## 6. Beispiel: Gesamtbild mit Kaskade

```toml
[git]
actions = ["git.fetch", "git.push", "mr.create", "mr.comment", "mr.update",
           "pipeline.trigger"]

[git.rules]
branch_prefixes     = ["claude/"]
max_open_mrs        = 5
max_writes_per_hour = 60

[[git.endpoint]]                        # voller Default: arbeiten + MRs + CI
host             = "gitlab.com"
type             = "gitlab"
allowed_projects = ["acme/infra", "acme/app"]

[[git.endpoint]]                        # Review-only: lesen + kommentieren
host             = "my-gitlab.de"
type             = "gitlab"
allowed_projects = ["acme/app"]
actions          = ["git.fetch", "mr.comment"]
rules            = { max_writes_per_hour = 30 }

[[git.endpoint]]                        # plain git: erbt Default ∩ type = fetch+push
host             = "personal-gitserver.it"
type             = "plain"
allowed_projects = ["me/dotfiles"]
```

## 7. Nicht tun

- **Kein** Read/Write-Split (`read_actions`/`write_actions`) und **keine**
  Namenskonvention (`write_push`) — Begründungen in §1.1.
- **Keine** Wildcards/Globs im Vokabular — neue Aktivierungen sind bewusste Edits.
- **Kein** `actions` innerhalb von `rules` — Menü vs. Drehregler (§1.3).
- **Keine** Header-Form `[git.actions]` (Wrapper-Tabelle oder Boolean-Map) — §1.3.
- **Keine** `[git.endpoint.rules]`-Header im Template — Umsortier-Footgun (§1.3).
- **Keine** action-adressierbare Read-Tabelle — Content-Exposure bleibt invariant (§2).
- **Keine** feldbasierten Actions (z.B. „mr.update ohne close") — Feld-Semantik
  gehört in die Capability-Schicht (§1.2).
- **Kein** `actions_add`/`actions_remove` — Listen ersetzen komplett (§1.4).
- **Keine** benannten Profile (`profile = "review-only"`) — nette spätere
  Zucker-Schicht über demselben Mechanismus, jetzt YAGNI.
- **Kein** Laufzeit-Reload der effektiven Tabellen (§4).

## 8. Umsetzungsstand

- **Vorhanden:** die globale REST-Write-Aktivierung (`[api.endpoints].enable` +
  `build_effective_table` mit FORBIDDEN-Backstop und `DEFAULT_ENABLED`) — sie wird
  zum per-Endpoint-Mechanismus umgebaut und liefert das halbe Vokabular.
- **Offen:** alles Übrige — Config-Feld + Kaskade + `type`-Schnitt, Action-Katalog
  (Gruppierung `mr.comment`, Transport-Verben), per-Host-Tabellen im `ApiGuard`,
  Action-Gate im git-Guard, Entfall `[api.endpoints]`, Template/`init`/`doctor`,
  Doktrin-Amendment in 08 §3.1, sowie Container-Tests (ein Endpoint voll, einer
  review-only, ein plain-Endpoint mit geerbtem Schnitt).

Der Schritt gilt erst als erledigt, wenn ein Multi-Endpoint-Deployment zwei Hosts
mit unterschiedlichen `actions` tatsächlich unterschiedlich behandelt (Container-Test)
und `[api.endpoints]` restlos entfernt ist. Begonnen wird erst **nach** Abschluss
von 08 (der Umsetzungs-Unterordner wird dann daraus abgeleitet).
