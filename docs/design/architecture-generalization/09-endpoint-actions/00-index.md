# 09 Endpoint-Actions — Umsetzung (für implementierende Agents)

Diese Dokumente leiten **exakt** aus dem Hauptdokument
[`../09-endpoint-actions.md`](../09-endpoint-actions.md) ab. Menschen lesen nur das
Hauptdokument (das *Was/Warum*); jeder Schritt hier ist das *Wie* eines Abschnitts
daraus. Bei Widerspruch gewinnt das Hauptdokument — melde den Widerspruch, statt zu
raten.

**Voraussetzung: 08-multi-target ist vollständig umgesetzt.** Dieser Schritt beginnt
erst danach. Er setzt die Endpoint-Taxonomie (`[[git.endpoint]]`, `type`,
`effective_rules`-Kaskade), das per-Host-Routing und `intent.host`-Verfügbarkeit in
beiden Guards voraus (08 §3, §1/§2, §5). Wenn ein Schritt hier eine dieser Grundlagen
nicht vorfindet, ist 08 nicht fertig — **stopp und melde das**, statt 08-Arbeit hier
nachzuholen.

## Reihenfolge (Abhängigkeiten)

**Die Nummer = die Abhängigkeitsebene.** Dateien mit **derselben** Nummer hängen nicht
voneinander ab und können **gleichzeitig** (parallel, je eigener Agent/Commit) gebaut
werden; eine höhere Nummer setzt alle niedrigeren voraus. Kein a/b — die geteilte Zahl
sagt „gleiche Ebene, keine Reihenfolge untereinander".

| Ebene | Datei | Leitet ab aus | Hängt ab von | Status |
| --- | --- | --- | --- | --- |
| 01 | [`01-action-catalog.md`](01-action-catalog.md) | §1.2, §5 (Action-Katalog) | — | offen |
| 01 | [`01-doctrine-amendment.md`](01-doctrine-amendment.md) | §1.3, §5 (Doktrin 08 §3.1) | — | offen |
| 02 | [`02-config-actions.md`](02-config-actions.md) | §1, §3, §5 (Config) | 01 (Katalog) | offen |
| 03 | [`03-rest-guard-per-host.md`](03-rest-guard-per-host.md) | §2, §5 (REST-Guard, `[api.endpoints]`-Entfall) | 01, 02 | offen |
| 03 | [`03-git-action-gate.md`](03-git-action-gate.md) | §2, §5 (git-Guard) | 01, 02 | offen |
| 04 | [`04-template-init-doctor.md`](04-template-init-doctor.md) | §4, §5 (Template/`init`/`doctor`) | 01–03 | offen |
| 05 | [`05-container-test.md`](05-container-test.md) | §8 (Fertig-Kriterium) | 01–04 | offen |

- **Ebene 01** — parallel: `01-action-catalog.md` (Warden-Python) **und**
  `01-doctrine-amendment.md` (reine Doku an 08 §3.1, ohne Code-Dep).
- **Ebene 02** — `02-config-actions.md` (Warden-Python), braucht den Katalog.
- **Ebene 03** — parallel: `03-rest-guard-per-host.md` **und** `03-git-action-gate.md`
  (beide Warden-Python; konsumieren nur Katalog + `effective_actions`, nicht einander).
- **Ebene 04** — `04-template-init-doctor.md` (CLI-/Asset-Schicht, `src/catraz/`).
- **Ebene 05** — `05-container-test.md` (Container-Integrationstest).

Jeder Schritt ist ein eigener Commit; **nicht** mehrere Schritte in einem Commit
vermischen — auch die zwei Dateien derselben Ebene bleiben getrennte Commits.

## Gemeinsamer Arbeitsablauf (für jeden Schritt gleich)

1. Das referenzierte `§` im Hauptdokument lesen **und** die im Schritt genannten
   Ist-Dateien, bevor du etwas änderst.
2. Änderung machen.
3. **Tests schreiben** (jeder Schritt nennt welche) — das Verhalten muss durch Tests
   belegt sein, nicht nur durch Augenschein.
4. Verifikation laufen lassen:
   - Warden-Schritte (1–4):
     ```bash
     cd warden
     uv run pytest -q
     uv run ruff check .
     uv run ruff format --check .
     uv run mypy
     ```
   - CLI-/Asset-Schritte (5, 7):
     ```bash
     uv run --with pytest python -m pytest tests/cli/ tests/container/ -q
     uv run mypy
     ```
   - Reiner Doku-Schritt (6): keine Verifikation außer Links/Referenzen prüfen.
5. Diff-Review.
6. **Ein Commit**, committe als Repo-Identität **`EddyXorb`** (schon so konfiguriert;
   nichts umstellen). **Kein** Co-Authorship-/`Generated-with`-Trailer. Der genaue
   Commit-Text steht in jedem Schritt unter „Commit".

## Kern-Doktrin (gilt überall in 09)

- **Drei-Schichten-Modell (§2), Reihenfolge nie umkehren:** Token = *kann*
  (Access-Mode, Token-Präsenz aus 08 §4.2, harte Decke), `actions` = *darf*
  (verengt nur darunter), Capabilities = *nie* (`core.capabilities.FORBIDDEN`,
  compiled-in, bleibt Backstop beim Tabellenbau). `actions` kann per Konstruktion
  nie eine FORBIDDEN-Capability oder einen fehlenden Token *aktivieren* — es kann
  nur wegnehmen.
- **Die REST-Read-Tabelle (`read_endpoints.py`) bleibt invariant** und ist
  **nicht** action-adressierbar (§2 Punkt 4). Nichts in 09 fasst sie an. `git.fetch`
  gated **nur** den git-Transport-Read, nie einen REST-Read.
- **Geschlossenes Vokabular, keine Wildcards, kein Read/Write-Split, keine
  Namenskonvention, kein `actions_add/remove`** (§1.1, §7). Listen **ersetzen
  komplett** (§1.4).
- **`actions` liegt neben `rules`, nie darin** (§1.3): host-unabhängiger Scope
  (`actions`) und Verhalten (`rules`) kaskadieren beide, aber getrennt.
- **Kein Laufzeit-Reload** (§4): effektive Tabellen/Actions werden **einmalig beim
  Start** gebaut (dieselbe Doktrin wie `build_effective_table` heute).
- **Fail-closed:** unbekannte/typ-widersprüchliche Action-IDs → `ConfigError`/
  `CatalogConfigError`, Start bricht ab (§3). Kohärenz-Probleme *zwischen* korrekten
  Actions sind **keine** Fehler — nur `doctor`-Warnungen (§4).
- **`[api.endpoints]` entfällt ersatzlos** (§5), pre-1.0, keine
  Rückwärtskompatibilität.

## Built-in-Default-Vokabular (die eine Quelle der Wahrheit, §1.2)

| Action | deckt ab | Art | im Default? |
| --- | --- | --- | --- |
| `git.fetch` | advertise(upload) + upload-pack | git-Transport, read | ✔ |
| `git.push` | advertise(receive) + receive-pack | git-Transport, write | ✔ |
| `mr.create` | Recognizer `mr.create` | REST write | ✔ |
| `mr.comment` | `mr.note`, `mr.discussion`, `mr.discussion_reply` | REST write | ✔ |
| `mr.update` | Recognizer `mr.update` | REST write | ✔ |
| `pipeline.trigger` | Recognizer `pipeline.trigger` | REST write | ✔ |
| `branch.create` | Recognizer `branch.create` | REST write | ✖ |
| `issue.create` | Recognizer `issue.create` | REST write | ✖ |

Der Built-in-Default (rechte Spalte) = heutiges `DEFAULT_ENABLED`
(`mr.create`, `mr.note`, `mr.discussion`, `mr.discussion_reply`, `mr.update`,
`pipeline.trigger`) **plus** die zwei Transport-Verben. Ein `warden.toml` ohne jeden
`actions`-Key verhält sich exakt wie heute — fehlender Key ≠ leere Liste.
