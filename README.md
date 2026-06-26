# claude-dev-env

Dockerisierte Claude Code Umgebung für autonome Agenten-Sessions, gesteuert über Remote Control.

> **Sicherheits-Umbau läuft (Stufe 01).** Der Agent-Container hält **kein GitLab-Token**
> mehr (R6). Der frühere `GITLAB_API_TOKEN`/`GITLAB_GIT_TOKEN`-Pfad im Agenten ist entfernt;
> GitLab-Zugriff kehrt in **Stufe 02** über einen Policy-Proxy („Warden") zurück. Vollständiges
> Design, Bedrohungsmodell und Roadmap: **[`docs/design/agentic-workflow/`](docs/design/agentic-workflow/README.md)**.

## Architektur (aktueller Stand)

```
Host (VSCode / Browser)
        │  Remote Control (claude.ai)
        ▼
┌─────────────────────┐
│   claude-dev-env    │   KEIN GitLab-Token (R6)
│  Claude Code        │
│  + Toolchain        │
│  (C++/Rust/Python)  │
└─────────────────────┘

GitLab: in Stufe 01 deaktiviert → kehrt in Stufe 02 über den Warden zurück
        (git Smart-HTTP-Proxy + REST-Filter, hält ALLE GitLab-Tokens).
        Siehe docs/design/agentic-workflow/README.md §11 (Ziel-Compose).
GitHub: vorerst nicht im Scope.
```

Der Agent hält seit Stufe 01 **kein** GitLab-Token mehr; GitHub ist vorerst nicht im Scope.

## Dateien

| Datei                | Zweck                                                                          |
| -------------------- | ------------------------------------------------------------------------------ |
| `docker-compose.yml` | Definiert alle Services und ihre Umgebungsvariablen                            |
| `Dockerfile`         | Build-Image: Ubuntu 24.04 mit Clang, Rust, Python/uv, Conan, Node, Claude Code |
| `entrypoint.py`      | Startet den Container (Claude-Home/Settings, dann `claude remote-control`)      |
| `.env`               | Secrets & Host-Werte (nicht committen) — Vorlage: `.env.example`               |
| `config/`            | Host-editierbare, nicht-geheime Konfiguration (read-only gemountet)            |
| `scripts/setup-dirs.sh` | Legt die Bind-Mount-Ordner an und setzt Rechte (§11.6)                      |
| `claude/`            | Persistierter Claude-Home (`~/.claude` im Container, bind-gemountet)           |
| `workspace/`         | Arbeitsverzeichnis für Projekte (bind-gemountet)                               |
| `docs/design/agentic-workflow/` | Design, Bedrohungsmodell, Umsetzungspläne (Warden, Forward-Proxy)  |

## Sicherheitsmaßnahmen

**Kein GitLab-Token im Agenten (R6, Stufe 01):** Jedes Credential im Prozessraum des Agenten
gilt als kompromittiert (siehe Design §3). Daher hält der `claude-dev-env`-Container **kein**
GitLab-Token; `GITLAB_API_TOKEN` und `GITLAB_GIT_TOKEN` sind aus Compose und `entrypoint.py`
entfernt. MCP-Tool-Sperren (`GITLAB_DENIED_TOOLS_REGEX` etc.) waren nur Config eines Tools,
dem der Agent vertraut werden müsste — die echte Grenze zieht ab Stufe 02 der Warden.

**Secrets vs. Config:** Geheimnisse nur in `.env` (gitignored). Nicht-geheime, host-editierbare
Konfiguration (Allowlist, Limits) liegt in **`config/`** (read-only gemountet, versioniert).
Laufzeitdaten in `state/`/`logs/` (Bind-Mounts, gitignored). Details: Design §11.

**Kein Root:** Claude Code läuft als unprivilegierter User `dev`. Der Entrypoint wechselt via
`gosu` zu diesem User, sobald `/workspace`-Ownership gesetzt ist.

## Einrichtung

### 1. Verzeichnisse anlegen

Die Bind-Mount-Ordner neben dem Compose-File anlegen und Schreibrechte setzen
(siehe Design §11.6):

```bash
./scripts/setup-dirs.sh
```

### 2. Claude-Credentials synchronisieren

Einmalig auf dem Host (Claude Code muss dort installiert und eingeloggt sein). **Dediziertes
Sandbox-Konto** verwenden, nicht das Primärkonto (Design §3.2):

```bash
python3 entrypoint.py sync
```

### 3. `.env` befüllen

`cp .env.example .env`, dann mindestens setzen:

```
ANTHROPIC_API_KEY=...
COMPOSE_PROFILES=             # leer — nur claude-dev-env (Stufe 01)
```

In **Stufe 01 hält der Agent kein GitLab-Token** — es sind keine GitLab-/GitHub-Tokens
einzutragen. GitLab-Zugriff (und die zugehörigen Read-/Write-Tokens, die dann nur der
**Warden** bekommt) folgt in Stufe 02; siehe `docs/design/agentic-workflow/` §11.

### 4. Starten

```bash
docker compose up -d
```

Der Agent ist danach über Remote Control unter claude.ai erreichbar.

### Rebuild nach Toolchain-Änderungen

```bash
docker compose build
docker compose up -d
```

## Konfiguration

Alle Versionen werden in `.env` gesteuert:

```
UV_VERSION=...
CLANG_VERSION=...
RUST_VERSION=...
CONAN_VERSION=...
NODE_VERSION=...
CLAUDE_CODE_VERSION=...
DEV_UID=...        # id -u auf dem Host, damit der bind-mount die richtigen Rechte hat
```
