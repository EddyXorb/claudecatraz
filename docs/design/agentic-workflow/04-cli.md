# 04 — `claudecatraz` (CLI)

> **Arbeitsname:** `claudecatraz` (Kurzform `catraz`) — der fähige, aber potenziell
> bösartige Agent sitzt auf der Insel und kommt nicht eigenmächtig runter.

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

`claudecatraz` macht aus dem Ritual **einen Vordereingang**:

```bash
claudecatraz init && claudecatraz up
```

---

## 2. Designprinzipien

| # | Prinzip | Konsequenz |
| - | ------- | ---------- |
| **P1** | **Ein Vordereingang.** Genau ein Binary, alles als Subcommand. | `setup-dirs.sh`, `entrypoint.py`, rohe `docker compose`-Aufrufe verschwinden hinter `claudecatraz`. |
| **P2** | **Minimale Pflichtfelder.** Faktisch zwingend sind nur 3 Werte (Anthropic-Key + 2 GitLab-Tokens). | Der Wizard fragt drei Dinge; UID, Versionen, Pfade, Profile werden defaulted und nur bei `--all` gezeigt. |
| **P3** | **`doctor` vor allem.** Der Preflight ist wichtiger als der Wizard. | Jede bekannte Falle (§5.2) wird mit klarer Meldung *vor* dem Start abgefangen. |
| **P4** | **Transparenz statt Blackbox.** Das CLI versteckt Komplexität, nicht die Sicherheitsgrenze. | Jeder zustandsändernde Befehl kann mit `--print` das exakte Compose/die Mounts zeigen, ohne auszuführen. |
| **P5** | **Konfig-Heimaten vereinheitlichen, nicht verschmelzen.** Der Split Secrets (`.env`) / Policy (`warden.toml`) bleibt — er *ist* das Sicherheitsmodell. | `policy`/`secrets` schreiben ins jeweils richtige File und kapseln die `WARDEN_*`-Präzedenz (siehe [`README.md`](./README.md) §11). |
| **P6** | **Idempotent.** Jeder Befehl darf beliebig oft laufen. | `init` überschreibt nie ungefragt (`--force` nötig); `doctor --fix` repariert nur Sicheres. |

---

## 3. Globale Optionen

Gelten für **alle** Subcommands:

| Flag | Wirkung |
| ---- | ------- |
| `-C, --dir <path>` | Projektwurzel (Default: Verzeichnis mit `docker-compose.yml`, hochgesucht). |
| `--print` / `--dry-run` | Zeigt die auszuführenden Compose-/Shell-Kommandos, führt nichts aus (P4). |
| `-y, --yes` | Nicht-interaktiv; alle Rückfragen mit Default beantworten. |
| `--json` | Maschinenlesbare Ausgabe (relevant für `doctor`, `status`, `policy show`). |
| `-v, --verbose` / `-q, --quiet` | Ausführlichkeit. |
| `--no-color` | Keine ANSI-Farben (für Logs/CI). |
| `-V, --version` | CLI- und Komponentenversionen (= `version`). |
| `-h, --help` | Hilfe; pro Subcommand verfügbar. |

Exit-Codes (einheitlich): `0` ok · `1` allgemeiner Fehler · `2` Konfig-/Validierungsfehler ·
`3` `doctor`-Check fehlgeschlagen · `4` Docker/Compose nicht verfügbar.

---

## 4. Befehlsübersicht

| Befehl | Zweck |
| ------ | ----- |
| `init` | Erstaufsetzen: Dirs, `.env`, Tokens, Sync, Validierung — der Wizard. |
| `doctor` | Preflight: prüft jede bekannte Setup-Falle, optional `--fix`. |
| `up` / `start` | Stack starten, auf Health warten, Remote-Control-URL ausgeben. |
| `down` / `stop` | Stack stoppen. |
| `restart` | Service(s) neu starten. |
| `status` / `ps` | Health, URLs, Quota-Snapshot. |
| `logs` | Logs folgen — Agent, Warden (Audit), Squid (access.log). |
| `sync` | Claude-Credentials des Sandbox-Kontos vom Host importieren. |
| `secrets` | `.env`-Geheimnisse setzen/prüfen, ohne Editor. |
| `policy` | Nicht-geheime Policy + Domain-Allowlist verwalten. |
| `shell` / `exec` | In einen Container droppen. |
| `verify` / `redteam` | Red-Team-Suite gegen den laufenden Stack fahren. |
| `update` | Images ziehen/neu bauen und neu starten. |
| `open` | Remote-Control- und Audit-Viewer-URL ausgeben/öffnen. |
| `completion` | Shell-Completion erzeugen. |
| `version` | Versionen ausgeben. |

---

## 5. Befehle im Detail

### 5.1 `init` — der Wizard

Idempotentes Erstaufsetzen. Reihenfolge: Dirs → `.env` aus `.env.example` → 3 Secrets
abfragen → `sync` → abschließendes `doctor`.

| Flag | Wirkung |
| ---- | ------- |
| `--minimal` | Fragt nur die 3 Pflicht-Secrets, übernimmt sonst alle Defaults (Default-Verhalten). |
| `--all` | Zeigt auch die optionalen Felder (Versionen, UID, Profile, Limits). |
| `--non-interactive` | Keine Prompts; Werte aus der Umgebung/`--set` lesen (CI). |
| `--set KEY=VALUE` | Einzelwert vorbelegen (wiederholbar). |
| `--uid <n>` | `DEV_UID` setzen (Default: `id -u`). |
| `--profile <name>` | `COMPOSE_PROFILES` (Default: `warden`). |
| `--force` | Bestehende `.env` überschreiben (sonst Abbruch mit Hinweis, P6). |
| `--skip-sync` | Den Claude-Credential-Import auslassen. |
| `--skip-doctor` | Abschluss-Preflight auslassen. |

Ausgabe am Ende: Zusammenfassung + nächste Aktion (`claudecatraz up`).

### 5.2 `doctor` — Preflight (der eigentliche Schmerzkiller)

Prüft die Punkte, die heute stumm scheitern. Jeder Befund: ✅ / ⚠️ / ❌ mit Einzeiler-Begründung **und** Fix-Hinweis.

| Sektion | Checks |
| ------- | ------ |
| `docker` | Docker-Daemon läuft, Compose v2 vorhanden, Image-Build möglich. |
| `net` | Ports frei (Audit-Viewer `9090`), `agent-net` ist `internal: true`. |
| `env` | `.env` existiert, `DEV_UID` == Owner der Bind-Mounts, Pfade vorhanden. |
| `tokens` | `ANTHROPIC_API_KEY` gesetzt; GitLab-Read/Write-Token gesetzt **und Scope plausibel** (read_api/api), gegen `GITLAB_URL` erreichbar. |
| `policy` | `warden.toml` valide; `allowed_projects` ohne Wildcard/Leaf/Group-Präfix (sonst startet Warden nicht); Limits numerisch; `WARDEN_*`-Overrides konsistent. |
| `allowlist` | `config/allowlist.txt` / `squid.conf` parsebar; Pflicht-Domains (npm, PyPI, crates) vorhanden. |
| `claude` | `CLAUDE_HOME` enthält gültige Sandbox-Credential (nicht das Primärkonto, §3.2). |

| Flag | Wirkung |
| ---- | ------- |
| `--fix` | Sicher reparierbare Befunde beheben: fehlende Dirs anlegen, `chown` auf `DEV_UID`. **Nie** Secrets/Policy ändern. |
| `--strict` | Warnungen (⚠️) zählen als Fehler → Exit `3`. |
| `--section <name>` | Nur eine Sektion (`docker`/`net`/`env`/`tokens`/`policy`/`allowlist`/`claude`). |
| `--json` | Befunde maschinenlesbar (CI-Gate). |

### 5.3 `up` / `start`

`docker compose up -d` + Health-Warten + URL-Ausgabe. Läuft implizit `doctor` (Sektionen
`docker`/`env`/`policy`) vorab, abschaltbar mit `--no-check`.

| Flag | Wirkung |
| ---- | ------- |
| `--build` | Images vorher neu bauen. |
| `--pull` | Basis-Images vorher ziehen. |
| `--no-wait` | Nicht auf Health warten (sofort zurück). |
| `--timeout <s>` | Health-Wartelimit (Default: 120). |
| `--no-check` | Den impliziten Vorab-`doctor` überspringen. |
| `--profile <name>` | Compose-Profil überschreiben. |
| `--print` | Nur das Compose-Kommando zeigen (P4). |

### 5.4 `down` / `stop`

| Flag | Wirkung |
| ---- | ------- |
| `-v, --volumes` | Auch Volumes entfernen. |
| `--keep-state` | `state/`-Bind-Mounts unangetastet lassen (Default). |
| `--print` | Nur Kommando zeigen. |

### 5.5 `restart`

`claudecatraz restart [service]` — Service oder ganzer Stack. Argument `service`:
`agent` | `warden` | `proxy` (Mapping auf `claude-dev-env`/`gitlab-warden`/`forward-proxy`).
Flag: `--build`.

### 5.6 `status` / `ps`

Health je Service, aufgelöste Remote-Control- und Audit-Viewer-URL, Quota-Snapshot des
Wardens (offene MRs/Branches, Writes/h gegen R5-Limits).

| Flag | Wirkung |
| ---- | ------- |
| `--json` | Maschinenlesbar. |
| `--watch` | Periodisch aktualisieren. |

### 5.7 `logs`

`claudecatraz logs [service]` mit semantischen Aliassen statt Container-Namen.

| Flag | Wirkung |
| ---- | ------- |
| `-f, --follow` | Folgen. |
| `--tail <n>` | Letzte n Zeilen (Default: 100). |
| `--since <dur>` | Zeitfenster (`10m`, `1h`). |
| `--audit` | Warden-Entscheidungslog (`logs/warden`) statt stdout. |
| `--squid` | Squid `access.log` (`logs/squid`) — wer wohin durfte/abgelehnt wurde. |

### 5.8 `sync`

Importiert die Credentials des Sandbox-Kontos vom Host nach `CLAUDE_HOME` (heute
`entrypoint.py sync`).

| Flag | Wirkung |
| ---- | ------- |
| `--from <path>` | Quell-`~/.claude` explizit angeben. |
| `--force` | Vorhandene Credential überschreiben. |
| `--check` | Nur prüfen, ob gültige Sandbox-Credential vorliegt (kein Schreiben). |

### 5.9 `secrets`

`.env`-Geheimnisse pflegen, ohne den Editor zu öffnen. Schreibt **nur** nach `.env` (P5).

| Subcommand | Wirkung |
| ---------- | ------- |
| `secrets set <KEY>` | Wert interaktiv (verdeckt) oder via `--value`/stdin setzen. |
| `secrets check` | Welche Secrets gesetzt/fehlen, mit Scope-Hinweis — nie den Wert ausgeben. |
| `secrets edit` | `.env` im `$EDITOR` öffnen (Fallback). |

Flags: `--value <v>` (für `set`; sonst Prompt), `--json` (für `check`).

### 5.10 `policy`

Nicht-geheime Policy + Domain-Allowlist. Kapselt **welche Datei** und die `WARDEN_*`-vs-`toml`-Präzedenz.

| Subcommand | Wirkung |
| ---------- | ------- |
| `policy show` | Effektive Policy (aufgelöst: env-Override *oder* toml), Quelle je Wert markiert. `--json`. |
| `policy add-project <path>…` | `allowed_projects` ergänzen; validiert **vollständigen Pfad ohne Wildcard/Leaf** (sonst Ablehnung, R6). |
| `policy rm-project <path>…` | Projekt entfernen. |
| `policy set <key> <value>` | `branch-prefix` (R2), `max-open-mrs`/`max-open-branches`/`max-writes-per-hour` (R5) setzen. |
| `policy allow <domain>…` | Domain zur Squid-Allowlist hinzufügen. |
| `policy disallow <domain>…` | Domain entfernen. |
| `policy domains` | Allowlist auflisten. |
| `policy lint` | `warden.toml` + Allowlist validieren (= `doctor --section policy,allowlist`). |

Flag (alle schreibenden): `--print` zeigt das resultierende Diff statt zu schreiben.
Hinweis: schreibende `policy`-Befehle erfordern danach `restart warden` (Hinweis wird ausgegeben).

### 5.11 `shell` / `exec`

`claudecatraz shell [service]` — interaktive Shell im Container (Default `agent`).
`claudecatraz exec [service] -- <cmd>` für einen Einzelbefehl.

| Flag | Wirkung |
| ---- | ------- |
| `--root` | Als root statt `dev` (Debug; Warnung). |
| `--` | Trennt CLI-Flags vom Container-Kommando. |

### 5.12 `verify` / `redteam`

Fährt die Red-Team-Suite aus [`03-testing-redteam.md`](./03-testing-redteam.md) gegen den
laufenden Stack (z. B. A1: „kein GitLab-Token im Agenten").

| Flag | Wirkung |
| ---- | ------- |
| `--suite <id>` | Nur eine Suite (`A`, `B`, …). |
| `--quick` | Nur schnelle Smoke-Checks. |
| `--json` | Maschinenlesbares Ergebnis (CI). |

### 5.13 `update`

| Flag | Wirkung |
| ---- | ------- |
| `--pull` | Neueste Basis-/Komponenten-Images ziehen. |
| `--build` | Neu bauen. |
| `--restart` | Nach Update neu starten (Default an, abschaltbar mit `--no-restart`). |

### 5.14 `open`, `completion`, `version`

- `open [target]` — `target` = `rc` (Remote Control auf claude.ai) | `audit` (Viewer
  `9090`). Ohne Argument: beide URLs ausgeben. `--print` druckt nur, statt zu öffnen.
- `completion <bash|zsh|fish>` — Completion-Skript auf stdout.
- `version` — CLI-Version + Komponentenversionen (Clang/Rust/Conan/Node/Claude Code aus `.env`).

---

## 6. Verteilung & Implementierung

- **Sprache:** Python — `entrypoint.py` ist bereits Python-mit-`argparse` und hat mit
  `sync`/`start` den Keim. Kein Go/Rust-Rewrite nötig, solange Docker ohnehin Voraussetzung ist.
- **Packaging:** `pyproject.toml` mit `console_scripts`-Entry-Point → `uv tool install .`
  / `uvx claudecatraz` (das Projekt nutzt schon `uv`). Im Repo-Root ein dünner
  `./claudecatraz`-Wrapper für den Zero-Install-Fall.
- **Innenleben:** dünne Schicht über `docker compose` (kein eigenes Orchestrieren); `init`
  zieht `setup-dirs.sh` ein; `sync` ruft die bestehende Logik. Das CLI generiert/liest
  Konfig, validiert und ruft Compose — es ersetzt keine dieser Schichten.

---

## 7. Verhältnis zum Sicherheitsmodell

Der Witz des Projekts ist die *nachvollziehbare* Isolation. Ein CLI, das `docker compose`
versteckt, dürfte sonst auch die Vertrauensgrenze verstecken. Gegenmittel:

- **P4 `--print`/`--dry-run`** auf jedem zustandsändernden Befehl — der Nutzer kann jederzeit
  sehen, welches Compose/welche Mounts laufen.
- Das CLI hält **keine** Geheimnisse und **bricht keine** Regel auf: `policy` kann z. B.
  keine Wildcard in `allowed_projects` schreiben (R6), `secrets` gibt nie einen Wert aus.
- `doctor`/`verify` machen das Sicherheitsmodell *prüfbar*, statt es zu verschleiern.

---

## 8. Umsetzungsreihenfolge

Inkrementell, jeder Schritt schon allein nützlich — kein Big-Bang.

| Schritt | Liefert |
| ------- | ------- |
| **1. `doctor`** | Sofortiger Schmerzkiller: stumme Fehler werden laut. Braucht noch kein `init`. |
| **2. `init`** | Drei Fragen statt `.env`-Scrollen. |
| **3. `up`/`down`/`status`/`logs`** | Compose-Komfort + URL-/Health-Feedback. |
| **4. `policy`/`secrets`** | Konfig-Heimaten unter einem Interface, Präzedenz gekapselt. |
| **5. `verify`/`update`/`completion`** | Reife: Prüfbarkeit, Wartung, Ergonomie. |

---

## 9. Offene Fragen

- **Name endgültig?** `claudecatraz`/`catraz` ist Arbeitsname — Binary-Name muss kurz & tippbar bleiben.
- **`doctor`-Tokenscope-Check:** Scope-Plausibilität ohne schreibenden GitLab-Call prüfen (read-only Probe gegen `GITLAB_URL`).
- **`policy set` → Reload:** automatischer `restart warden` nach Policy-Änderung, oder nur Hinweis (aktuell: Hinweis)?
