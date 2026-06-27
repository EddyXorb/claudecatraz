# 04 — `catraz` (CLI)

> **Befehl:** `catraz` — der fähige, aber potenziell bösartige Agent sitzt auf der
> Insel und kommt nicht eigenmächtig runter. Das CLI ist der Vordereingang.

Status: ⏳ geplant. Voraussetzung: Stufen 01/02 implementiert. Parallel zu Stufe 03 baubar.

---

## 1. Motivation

Heute ist das Aufsetzen ein **4-Schritte-Ritual über drei Werkzeuge** plus zwei Konfig-Heimaten mit Präzedenz-Regeln:

```bash
./scripts/setup-dirs.sh          # Bash: Dirs + chown
python3 entrypoint.py sync       # Python: Claude-Credentials importieren
cp .env.example .env && $EDITOR  # Hand: ~15 Variablen, davon 3 zwingend
docker compose up -d             # Compose: Start, ohne Health-/URL-Feedback
```

Die Schwere liegt nicht im Tippen, sondern im **stummen Scheitern**: falsche `DEV_UID`,
Token mit falschem Scope, belegter Port, eine Wildcard in `allowed_projects`, die den
Warden gar nicht erst starten lässt. Es gibt keinen Ort, der vor dem Start sagt: *„so
wird das nichts, und zwar deshalb".*

`catraz` macht aus dem Ritual **einen Vordereingang**:

```bash
catraz init     # fragt 3 Geheimnisse, legt Dirs an, importiert Credentials, prüft
catraz up       # startet, wartet auf Health, druckt die Remote-Control-URL
```

---

## 2. Was diese Skizze von der ersten unterscheidet (Vereinfachung)

Die erste Skizze listete **16 Top-Level-Befehle** (`init`, `doctor`, `up`, `down`,
`restart`, `status`, `logs`, `sync`, `secrets`, `policy`, `shell`, `verify`, `update`,
`open`, `completion`, `version`). Das erschlägt: Wer zum ersten Mal `catraz --help`
tippt, sieht eine Wand und weiß nicht, *welche zwei* Befehle ihn ans Ziel bringen.

Diese Skizze reduziert auf **sieben Befehle** — und keiner davon ist optional zum
Loslaufen oder Betreiben. Leitgedanke: **Was muss ein Mensch wirklich tippen, um den
Stack hochzubringen und am Laufen zu halten?** Alles andere ist entweder ein Flag, eine
direkte Datei-Bearbeitung oder gehört nicht ins Start-Werkzeug.

### Geprüfte Ansätze (aus Sicht des Benutzers)

| Ansatz | Bewertung |
| ------ | --------- |
| **A. compose-Mental-Model + 2 Essentials** | ✅ **Gewählt.** Wer den Stack betreibt, kennt `up`/`down`/`logs`/`ps` bereits aus `docker compose`. Wir übernehmen genau dieses Vokabular und ergänzen nur die zwei projekteigenen Dinge: den Wizard (`init`) und den Preflight (`doctor`). Nullkomma Lernkurve. |
| **B. Interaktives TUI-Menü** (`catraz` öffnet ein Menü) | ❌ Nett für den Erstkontakt, aber nicht skriptbar, schlecht für CI/headless und paradoxerweise *weniger* offensichtlich für jemanden, der ohnehin im Terminal lebt. |
| **C. Zwei Befehle** (`setup` + `run`) | ❌ Zu mager. Versteckt `doctor` (den eigentlichen Schmerzkiller) und `logs` (das „warum startet es nicht"-Werkzeug). |

### Was wo hingewandert ist

| Alter Befehl | Jetzt |
| ------------ | ----- |
| `restart`, `update` | Flags an `up`: `catraz up --build` (neu bauen), `catraz up --pull` (Basis-Images ziehen). `up` recreatet ohnehin. |
| `secrets`, `policy` | **Wizard schreibt `.env`, Editor schreibt `config/warden.toml`.** Geheimnisse und der projekt­spezifische Allowlist-Override leben in `.env` (gitignored, der Wizard füllt sie validiert); die nicht-geheime Policy-Vorlage in `config/warden.toml` (hand-editierbar). `doctor` **validiert** beide laut. Ein guarded `policy add-project` für den seltenen „später ein zweites Projekt"-Fall ist bewusst aufgeschoben — der Wizard deckt den Konstruktionsfall ab (§2.2). |
| `verify`/`redteam` | Bleibt das Skript `tests/redteam/` (Stufe 03). Gehört in CI/Prüfung, nicht ins Start-Werkzeug. |
| `shell`/`exec` | `docker compose exec <service> bash` — Standard-Compose, kein Mehrwert durch Wrapping. |
| `open` | Die URLs druckt `up` am Ende und `status` jederzeit. Kein eigener Befehl. |
| `completion`, `version` | `catraz --version` (global). Completion ist Reife-Politur, kein Start-Essential — später. |

**Behalten als eigener Befehl:** `sync` — der Claude-Credential-Import ist
*wiederkehrende Wartung*, nicht Erstaufsetzen (Sandbox-Tokens laufen ab). Ihn im Wizard
zu vergraben hieße, für eine 2-Uhr-nachts-Neuanmeldung den ganzen Assistenten zu
durchlaufen. `init` faltet `sync` für den Erstlauf mit ein, aber `catraz sync` bleibt der
direkte Weg.

Ergebnis: **16 → 7**, unter der Hälfte, ohne dass der Weg „von Null bis laufender Stack"
länger wird — er besteht aus genau zwei Befehlen (`init`, `up`).

### 2.2 Nach dem Roast — was sich geändert hat

Ein Review-Durchgang („roast") hat die erste Vereinfachung hart geprüft. Die
überzeugenden Befunde sind eingearbeitet:

| Befund | Konsequenz |
| ------ | ---------- |
| **`init → up` scheitert für jeden:** Der Wizard fragte 3 Secrets, aber **nicht** `allowed_projects` — und der Warden startet bei leerer Allowlist gar nicht (fail-closed). Der Vorzeige-Pfad brach also auf Befehl zwei. | `init` fragt jetzt zusätzlich die **erlaubten Projekt-Pfade** ab, validiert sie (kein Wildcard/Leaf/Group-Präfix) und schreibt sie als `WARDEN_ALLOWED_PROJECTS` nach `.env`. Damit ist der validierte Konstruktionsfall abgedeckt. |
| **`sync` versteckt:** Credential-Ablauf ist wiederkehrende Wartung. | `sync` bleibt eigener Befehl (s. o.). |
| **Stummster Fehler fehlte in `doctor`:** Ist `COMPOSE_PROFILES` leer/verbogen, startet der Stack **ohne Warden** (`depends_on … required: false`) — „läuft", aber ohne Vertrauensgrenze. | Neuer `doctor`-Check `compose`: prüft, dass der Warden im aktiven Profil ist. |
| **Token-Check war Theater** („gesetzt/nicht gesetzt" fängt den realen Fehler — vertauschte/abgelaufene/falsch-gescopte Tokens — nicht). | `doctor tokens` macht eine **Best-Effort-Online-Probe** vom Host (`/api/v4/user` + Scope-Read), erkennt ungültige/vertauschte Tokens; fällt offline sauber auf „gesetzt/nicht gesetzt" zurück. |
| **Generischer Owner-Check** statt der *konkreten* Falle, die `entrypoint.py` schon kennt (von Docker als root angelegtes `CLAUDE_HOME`). | `doctor` portiert genau diesen Guard. |
| **`catraz` ohne Argument startete einen mutierenden Wizard** (überraschend, fragt Secrets). | Ohne Argument → **Hilfe** (read-only, mutiert nie). |
| **`up --no-check`** ließ ausgerechnet die Sicherheits-Preflight abschalten. | `--no-check` entfällt; die sicherheitskritischen Checks (`policy`/`compose`) laufen immer. |
| **Exit-Codes/`--print` unscharf.** | Implizites `doctor`-Scheitern in `up` → Exit `3`. `--print` gilt nur für Compose-aufrufende Befehle. |

Bewusst **nicht** übernommen: ein eigener `policy add-project`-Befehl (der Wizard liefert
den validierten Konstruktionspfad; `doctor` fängt Hand-Edits) und das Aufrufen des
Warden-eigenen Parsers aus `doctor` (würde catraz an Warden-Interna koppeln und einen
nicht existierenden `--validate-config`-Flag erfinden). Stattdessen schreibt catraz
**nie** TOML — es schreibt ausschließlich `.env` — und `doctor` ist ehrlich als
*schneller Vor-Check* dokumentiert; maßgeblich bleibt der Reconcile des Wardens.

---

## 3. Designprinzipien

| # | Prinzip | Konsequenz |
| - | ------- | ---------- |
| **P1** | **Ein Vordereingang.** Genau ein Binary, alles als Subcommand. | `setup-dirs.sh`, `entrypoint.py sync`, rohe `docker compose`-Aufrufe verschwinden hinter `catraz`. |
| **P2** | **Minimale Pflichtfelder.** Faktisch zwingend sind nur 3 Werte (Anthropic-Key + 2 GitLab-Tokens). | Der Wizard fragt drei Dinge; UID, Versionen, Pfade werden defaulted. |
| **P3** | **`doctor` vor allem.** Der Preflight ist wichtiger als der Wizard. | Jede bekannte Falle (§5.2) wird mit klarer Meldung *vor* dem Start abgefangen. `up` ruft ihn implizit. |
| **P4** | **Transparenz statt Blackbox.** Das CLI versteckt Komplexität, nicht die Sicherheitsgrenze. | Jeder zustandsändernde Befehl kann mit `--print` das exakte Compose-Kommando zeigen, ohne auszuführen. |
| **P5** | **Konfig-Heimaten unangetastet.** Der Split Secrets (`.env`) / Policy (`warden.toml`) bleibt — er *ist* das Sicherheitsmodell. | Das CLI **schreibt** Secrets nur über den Wizard nach `.env` und **liest/validiert** beide Dateien in `doctor`. Es verschmilzt sie nie. |
| **P6** | **Idempotent.** Jeder Befehl darf beliebig oft laufen. | `init` fragt nur, was fehlt, und überschreibt gesetzte Werte nur mit Rückfrage; `doctor --fix` repariert nur Sicheres (Dirs/`chown`), nie Secrets/Policy. |
| **P7** | **Null-Install lauffähig.** `./catraz` im Repo-Root, reine Python-Standardbibliothek. | Kein `pip install`, kein `uv`-Schritt nötig, um den Stack hochzubringen. Docker ist ohnehin Voraussetzung. |

---

## 4. Befehlsübersicht

```
catraz                      # ohne Argument: Hilfe (read-only, mutiert nie)
catraz init                 # Wizard: Dirs, .env (3 Secrets + Projekte), Sync, Doctor
catraz doctor [--fix]       # Preflight: jede bekannte Setup-Falle, laut statt stumm
catraz up [--build]         # starten, auf Health warten, Remote-Control-URL drucken
catraz down [-v]            # stoppen
catraz status               # Health je Service, URLs, Quota-Snapshot
catraz logs [service] [-f]  # Logs — agent | warden | proxy | --audit
catraz sync                 # Claude-Credentials (Sandbox-Konto) vom Host re-importieren
```

**Globale Optionen** (alle Subcommands):

| Flag | Wirkung |
| ---- | ------- |
| `-C, --dir <path>` | Projektwurzel (Default: Verzeichnis mit `docker-compose.yml`, hochgesucht). |
| `--print` / `--dry-run` | Zeigt das auszuführende Compose-Kommando, führt nichts aus (P4). Wirkt nur bei Compose-aufrufenden Befehlen (`up`/`down`). |
| `-y, --yes` | Nicht-interaktiv; Rückfragen mit Default beantworten (CI). |
| `--no-color` | Keine ANSI-Farben. |
| `-V, --version` | CLI-Version + Komponentenversionen aus `.env`. |
| `-h, --help` | Hilfe; pro Subcommand verfügbar. |

**Exit-Codes:** `0` ok · `1` allgemeiner Fehler · `2` Konfig-/Validierungsfehler ·
`3` `doctor`-Check fehlgeschlagen · `4` Docker/Compose nicht verfügbar.

---

## 5. Befehle im Detail

### 5.1 `init` — der Wizard (die interaktive Session)

Das Herzstück der Vereinfachung: **eine Sitzung, in der man die nötigen Dinge eingibt
und damit alles zum Laufen bringt.** Idempotent. Reihenfolge:

1. **Dirs anlegen** — `config/ state/ logs/ workspace/ claude/` mit `chown` auf `DEV_UID`
   (zieht `setup-dirs.sh` ein).
2. **`.env` sicherstellen** — aus `.env.example` kopieren, falls nicht vorhanden.
3. **Drei Geheimnisse abfragen** — `ANTHROPIC_API_KEY`, `GITLAB_READ_TOKEN`,
   `GITLAB_WRITE_TOKEN`. Eingabe verdeckt (kein Echo). Bereits gesetzte Werte werden
   angezeigt (maskiert) und nur auf Rückfrage überschrieben (P6).
4. **Erlaubte Projekte abfragen** — die Projekt-Pfade, an denen der Agent arbeiten darf
   (z. B. `group/sub/proj`). Jeder Pfad wird sofort validiert (kein Wildcard, kein
   Leaf-/Group-Präfix) und nach `WARDEN_ALLOWED_PROJECTS` in `.env` geschrieben. **Ohne
   diesen Schritt startet der Warden nicht** (fail-closed) — er gehört darum in den Wizard,
   nicht in eine spätere Hand-Edition.
5. **Credentials importieren** — `entrypoint.py sync` (Claude-Sandbox-Konto vom Host).
6. **Abschluss-`doctor`** — Preflight; am Ende die nächste Aktion ausgeben (`catraz up`).

| Flag | Wirkung |
| ---- | ------- |
| `--force` | Auch gesetzte `.env`-Werte ohne Rückfrage neu abfragen. |
| `--skip-sync` | Den Claude-Credential-Import auslassen. |
| `-y, --yes` | Nicht-interaktiv: Werte aus der Umgebung lesen, nichts fragen (CI). |

Eine Eingabe wird nie verlangt, die schon gültig ist — wer `init` erneut ausführt, soll
nur die fehlenden Lücken füllen müssen.

### 5.2 `doctor` — Preflight (der eigentliche Schmerzkiller, P3)

Prüft die Punkte, die heute stumm scheitern. Jeder Befund: ✅ / ⚠️ / ❌ mit
Einzeiler-Begründung **und** Fix-Hinweis.

| Sektion | Checks |
| ------- | ------ |
| `docker` | Docker-Daemon läuft, Compose v2 vorhanden. |
| `compose` | `COMPOSE_PROFILES`/Compose-Auflösung enthält den **Warden** — sonst „läuft" der Stack ohne Vertrauensgrenze (`depends_on … required: false`). |
| `env` | `.env` existiert, `DEV_UID` == Owner der Bind-Mounts, Schreib-Dirs vorhanden. |
| `tokens` | `ANTHROPIC_API_KEY` + beide GitLab-Tokens gesetzt (Wert wird nie ausgegeben). **Best-Effort-Online-Probe** vom Host gegen `GITLAB_URL`: Token gültig/nicht abgelaufen, Scopes plausibel (Read-Token nicht `api`-schreibend, Write-Token mit `api`), vertauschte Tokens erkannt. Offline → Rückfall auf „gesetzt/nicht gesetzt". |
| `policy` | resolvierte `allowed_projects` (`.env`-Override *oder* `warden.toml`) ohne Wildcard/Leaf/Group-Präfix und nicht leer (sonst startet Warden nicht); Limits numerisch. Schneller Vor-Check — maßgeblich bleibt der Warden-Reconcile. |
| `claude` | `CLAUDE_HOME` enthält eine Sandbox-Credential und ist **nicht von root** angelegt (die konkrete Falle aus `entrypoint.py cmd_sync`). |
| `net` | Audit-Viewer-Port frei. |

| Flag | Wirkung |
| ---- | ------- |
| `--fix` | Sicher reparierbare Befunde beheben: fehlende Dirs anlegen, `chown` auf `DEV_UID`. **Nie** Secrets/Policy ändern (P5/P6). |
| `--strict` | Warnungen (⚠️) zählen als Fehler → Exit `3`. |

### 5.3 `up`

`docker compose up -d` + auf Health warten + URL-Ausgabe. Läuft **immer** zuerst die
sicherheitskritischen `doctor`-Sektionen (`docker`/`compose`/`env`/`policy`) — scheitert
einer mit ❌, bricht `up` mit Exit `3` ab. Dieser Preflight ist *nicht* abschaltbar
(früheres `--no-check` entfällt): er ist genau das, was den Stack mit Vertrauensgrenze
hochbringt.

| Flag | Wirkung |
| ---- | ------- |
| `--build` | Images vorher neu bauen (ersetzt das alte `update`/`restart --build`). |
| `--pull` | Basis-Images vorher ziehen. |
| `--no-wait` | Nicht auf Health warten (Preflight läuft trotzdem). |
| `--print` | Nur das Compose-Kommando zeigen (P4). |

Am Ende: Remote-Control-URL (claude.ai) und Audit-Viewer-URL.

### 5.4 `down`

`docker compose down`. Flag `-v, --volumes` entfernt auch Volumes; `state/`-Bind-Mounts
bleiben per Default unangetastet. `--print` zeigt nur das Kommando.

### 5.5 `status`

Health je Service, aufgelöste Remote-Control- und Audit-Viewer-URL, Quota-Snapshot des
Wardens (offene MRs/Branches gegen R5-Limits). Ist nichts eingerichtet, weist `status`
auf `catraz init` hin.

### 5.6 `logs`

`catraz logs [service]` mit semantischen Aliassen statt Container-Namen
(`agent`→`claude-dev-env`, `warden`→`gitlab-warden`, `proxy`→`forward-proxy`).

| Flag | Wirkung |
| ---- | ------- |
| `-f, --follow` | Folgen. |
| `--tail <n>` | Letzte n Zeilen (Default: 100). |
| `--audit` | Warden-Entscheidungslog (`logs/warden/*.jsonl`) statt Container-stdout. |

### 5.7 `sync`

Importiert die Credentials des Sandbox-Kontos vom Host nach `CLAUDE_HOME` (wrappt
`entrypoint.py sync`). Eigener Befehl, weil Token-Ablauf wiederkehrende Wartung ist — man
will dafür nicht den ganzen Wizard durchlaufen.

| Flag | Wirkung |
| ---- | ------- |
| `--from <path>` | Quell-`~/.claude` explizit angeben (Default: `~/.claude`). |
| `--force` | Vorhandene Credential überschreiben. |

---

## 6. Verteilung & Implementierung

- **Sprache:** Python, reine Standardbibliothek (`argparse`, `subprocess`, `getpass`).
  `entrypoint.py` ist bereits Python-mit-`argparse` — `catraz` ruft dessen `sync` wieder.
- **Null-Install (P7):** ein ausführbares `./catraz` im Repo-Root. Kein `pip`/`uv`-Schritt
  nötig, um den Stack hochzubringen. (Ein optionaler `pyproject.toml`-Entry-Point für
  `uv tool install` ist spätere Politur, kein Blocker.)
- **Innenleben:** dünne Schicht über `docker compose` (kein eigenes Orchestrieren). Das CLI
  generiert/liest Konfig, validiert und ruft Compose — es ersetzt keine dieser Schichten.

---

## 7. Verhältnis zum Sicherheitsmodell

Der Witz des Projekts ist die *nachvollziehbare* Isolation. Ein CLI, das `docker compose`
versteckt, dürfte sonst auch die Vertrauensgrenze verstecken. Gegenmittel:

- **P4 `--print`/`--dry-run`** auf jedem zustandsändernden Befehl — der Nutzer sieht
  jederzeit, welches Compose-Kommando läuft.
- Das CLI hält **keine** Geheimnisse dauerhaft und bricht **keine** Regel auf: Es
  **schreibt ausschließlich `.env`** (Secrets + der `WARDEN_ALLOWED_PROJECTS`-Override) —
  nie eine Datei in `config/` (§11 README), nie TOML. `config/warden.toml` wird nur
  *gelesen*, um es zu validieren. `doctor` gibt nie einen Secret-Wert aus.
- `doctor` macht das Sicherheitsmodell *prüfbar*, statt es zu verschleiern.

---

## 8. Umsetzungsreihenfolge

Inkrementell, jeder Schritt schon allein nützlich — kein Big-Bang.

| Schritt | Liefert |
| ------- | ------- |
| **1. `doctor`** | Sofortiger Schmerzkiller: stumme Fehler werden laut. Braucht noch kein `init`. |
| **2. `init`** | Drei Fragen statt `.env`-Scrollen. |
| **3. `up`/`down`/`status`/`logs`** | Compose-Komfort + URL-/Health-Feedback. |
| **4. Politur** | `--version`-Komponenten, Shell-Completion, optionaler `uv`-Entry-Point. |

---

## 9. Offene Fragen

- **`doctor`-Tokenscope-Check:** Scope-Plausibilität ohne schreibenden GitLab-Call prüfen
  (read-only Probe gegen `GITLAB_URL`) — vorerst nur „gesetzt/nicht gesetzt".
- **Audit-Viewer-URL:** `172.31.0.2:9090` ist nur vom Host erreichbar; `status`/`up`
  drucken sie mit dem Hinweis auf den `socat`-Tunnel (README §„Audit log in the browser").
</content>
</invoke>
