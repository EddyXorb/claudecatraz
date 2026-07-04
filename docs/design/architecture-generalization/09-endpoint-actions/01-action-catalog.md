# 01 — Action-Katalog: Action-ID → Recognizer/Transport, plus Built-in-Default

**Leitet ab aus** [`../09-endpoint-actions.md`](../09-endpoint-actions.md) §1.2 und §5
(„Action-Katalog"). Lies §1.2 zuerst — besonders „Action ≠ Recognizer" und die
Granularitätsgrenze.

## Ziel

Ein **in Code definierter, geschlossener** Katalog, der jede Action-ID auf die Menge
der Recognizer-IDs bzw. git-Transport-Operationen abbildet, die sie abdeckt — plus
den Built-in-Default und das `type`-abhängige Vokabular. Das ist die
Agent-Granularität aus §1.2 („wer `mr.note` aktiviert und `mr.discussion_reply`
vergisst, hätte einen Agenten, der Review-Threads nicht beantworten kann"). Die
Abbildung ist **nicht** konfigurierbar.

Dieser Schritt liefert nur die reinen Daten + Helfer. Verdrahtet wird der Katalog in
02 (Config-Validierung), 03 (REST-Guard) und 03 (git-Guard).

## Ist-Zustand

- `warden/guards/gitlab_api/catalog/write_endpoints.py` definiert die Recognizer
  (`mr.create`, `mr.note`, `mr.discussion`, `mr.discussion_reply`, `mr.update`,
  `pipeline.trigger`, `branch.create`, `issue.create`) und `DEFAULT_ENABLED`
  (die ersten sechs).
- Es gibt **keine** Abstraktion oberhalb der Recognizer-Ebene. Der git-Transport
  hat gar keine katalogisierte Repräsentation.

## Platzierungs-Konvention (verbindlich)

`actions.py` liegt **immer im selben Ordner wie `guard.py`** des jeweiligen Guards —
**nicht** in einem `catalog/`-Unterordner. Also:

- Forge-Seite: `warden/guards/gitlab_api/actions.py` (neben
  `warden/guards/gitlab_api/guard.py`).
- git-Seite: `warden/guards/git/actions.py` (neben `warden/guards/git/guard.py`).

> Das weicht bewusst vom **illustrativen** Pfad im Hauptdokument §5 ab
> (`guards/gitlab_api/catalog/actions.py`) — §5 nennt den Katalog-Ordner nur als
> Beispiel für „neben dem Katalog". Die Konvention „`actions.py` neben `guard.py`"
> ist die maßgebliche Regel: die Action-Ebene ist Guard-Granularität und gehört zum
> Guard, nicht in dessen `catalog/`-Interna. Die Datei **importiert** aus
> `catalog/` (Recognizer/`WRITE_ENDPOINTS`), lebt aber eine Ebene höher.

## Umsetzung

1. **Neues Modul `warden/guards/gitlab_api/actions.py`** (Forge-Seite, neben
   `guard.py` — siehe Platzierungs-Konvention oben):
   - Eine unveränderliche Abbildung `ACTION_TO_RECOGNIZERS: Mapping[str, tuple[str, ...]]`
     für die REST-Actions gemäß Tabelle §1.2:
     - `mr.create` → `("mr.create",)`
     - `mr.comment` → `("mr.note", "mr.discussion", "mr.discussion_reply")`
     - `mr.update` → `("mr.update",)`
     - `pipeline.trigger` → `("pipeline.trigger",)`
     - `branch.create` → `("branch.create",)`
     - `issue.create` → `("issue.create",)`
   - **Validierung beim Import (Modul-Konsistenz):** jede referenzierte Recognizer-ID
     muss in `WRITE_ENDPOINTS` existieren, und jede `WRITE_ENDPOINTS`-ID muss von
     **genau einer** Action abgedeckt sein (keine verwaiste, keine doppelt gemappte
     Recognizer-ID). Verletzung → harter Fehler beim Import (Programmierfehler, kein
     `ConfigError`). Das ist der Test aus §1.2 in ausführbarer Form.
2. **Git-Transport-Verben.** Definiere die zwei Transport-Action-IDs im Modul
   `warden/guards/git/actions.py` (neben `guards/git/guard.py` — dieselbe
   Platzierungs-Konvention): `GIT_FETCH = "git.fetch"`, `GIT_PUSH = "git.push"`
   und einem Helfer, der eine git-Operation (`advertise`/`upload-pack`/`receive-pack`
   + `service`) auf die Action-ID abbildet (§1.2/§5):
   - `advertise` mit `service == "git-upload-pack"` **und** `upload-pack` → `git.fetch`
   - `advertise` mit `service == "git-receive-pack"` **und** `receive-pack` → `git.push`
   Dieser Helfer wird in 04 benutzt; hier nur definieren und testen.
3. **Der Built-in-Default als eine benannte Konstante.** Eine Funktion oder
   Konstante, die die volle Default-Action-Liste liefert (§1.2 rechte Spalte):
   `("git.fetch", "git.push", "mr.create", "mr.comment", "mr.update", "pipeline.trigger")`.
   Sie muss **abgeleitet** konsistent mit `DEFAULT_ENABLED` sein — schreibe einen Test,
   der belegt, dass die REST-Actions im Default genau die Recognizer aus
   `DEFAULT_ENABLED` aufspannen (so bleibt „verhält sich exakt wie heute" beweisbar).
4. **`type`-abhängiges Vokabular (§3.2).** Ein Helfer
   `actions_valid_for_type(type: str) -> frozenset[str]`:
   - `plain` → `{git.fetch, git.push}` (kein `mr.*`/`pipeline.*`/`issue.*`).
   - `gitlab` → alle acht IDs.
   - `github` → reserviert; solange kein GitHub-Guard existiert, wie in 08 behandelt
     (bekannter, aber nicht-implementierter Typ — hier nicht „durchwinken", sondern
     denselben Weg wie 08 §3 für `github` gehen; kläre am 08-Ist-Stand, ob `github`
     überhaupt bis zur Config-Ebene kommt).
   Die *Menge aller gültigen IDs* (`git.*` ∪ Forge-Actions) ist ebenfalls hier zentral
   ableitbar — 02 braucht sie für „unbekannte Action-ID → `ConfigError`".

## Nicht tun

- **Keine** Wildcards, kein Read/Write-Split, keine Namenskonvention — nur die
  wörtlichen IDs aus der Tabelle (§1.1, §7).
- **Keine** Abbildung feiner als ein Recognizer — `mr.update` bleibt ein Block
  (Titel-Edit *und* `state_event=close`); Feld-Semantik gehört in die
  Capability-Schicht, nie hierher (§1.2 Granularitätsgrenze).
- Die Abbildung **nicht** konfigurierbar machen — sie lebt in Code, neben dem Katalog.
- Die **Read-Tabelle** (`read_endpoints.py`) **nicht** referenzieren — Actions
  adressieren sie nie (§2).
- `[api.endpoints]`/`build_effective_table` hier **nicht** anfassen — das ist 03 (REST-Guard).

## Tests

`warden/tests/test_action_catalog.py` (neu):
- `ACTION_TO_RECOGNIZERS` deckt jede `WRITE_ENDPOINTS`-ID **genau einmal** ab; keine
  unbekannte Recognizer-ID referenziert (der Konsistenz-Invariant aus §1.2).
- `mr.comment` deckt exakt `{mr.note, mr.discussion, mr.discussion_reply}` ab.
- Der Built-in-Default spannt (auf der Recognizer-Ebene) genau `DEFAULT_ENABLED` auf,
  plus `git.fetch`/`git.push` als Transport.
- `actions_valid_for_type("plain") == {git.fetch, git.push}`;
  `actions_valid_for_type("gitlab")` enthält alle acht.
- Transport-Mapping: advertise(upload)/upload-pack → `git.fetch`;
  advertise(receive)/receive-pack → `git.push`.

## Verifikation

`cd warden && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`

## Commit

Als `EddyXorb`, kein Co-Author-Trailer. Nachricht:

```
feat(catalog): action catalog — action-id to recognizer/transport mapping
```

## Fertig-Kriterium

Der Action-Katalog existiert als geschlossene, in Code getestete Abbildung
(Action-ID → Recognizer-IDs bzw. git-Ops), der Built-in-Default und
`actions_valid_for_type` sind ableitbar, die Modul-Konsistenz ist per Test belegt.
Noch **nichts** konsumiert den Katalog — das kommt in 02 und den beiden 03er-Schritten.
