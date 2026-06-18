# claude-dev-env

Dockerisierte Claude Code Umgebung für autonome Agenten-Sessions, gesteuert über Remote Control. Enthält einen GitLab MCP Sidecar als einzige Schnittstelle zur GitLab-API.

## Architektur

```
Host (VSCode / Browser)
        │  Remote Control (claude.ai)
        ▼
┌─────────────────────┐        ┌──────────────────────┐
│   claude-dev-env    │──MCP──▶│     gitlab-mcp       │
│                     │        │                      │
│  Claude Code        │        │  zereight/gitlab-mcp │
│  + Toolchain        │        │  GITLAB_API_TOKEN    │──▶ gitlab.com API
│  (C++/Rust/Python)  │        │  (api scope)         │
└─────────────────────┘        └──────────────────────┘
        │
        │ git over HTTPS
        ▼ (GITLAB_GIT_TOKEN, read/write_repository)
   gitlab.com
```

Der Agent hat **keinen direkten Zugriff** auf den GitLab API-Token. Alle GitLab-Operationen laufen über den MCP-Sidecar.

## Dateien

| Datei | Zweck |
|-------|-------|
| `docker-compose.yml` | Definiert beide Services und ihre Umgebungsvariablen |
| `Dockerfile` | Build-Image: Ubuntu 24.04 mit Clang, Rust, Python/uv, Conan, Node, Claude Code |
| `entrypoint.py` | Startet den Container; konfiguriert git-Credentials und MCP-Verbindung |
| `.env` | Tokens und Konfiguration (nicht committen) |
| `claude/` | Persistierter Claude-Home (`~/.claude` im Container, bind-gemountet) |
| `workspace/` | Arbeitsverzeichnis für Projekte (bind-gemountet) |

## Sicherheitsmaßnahmen

**Token-Trennung:** Zwei separate Tokens mit minimalen Scopes.
- `GITLAB_API_TOKEN` (nur im MCP-Container): `api`-Scope für alle GitLab-API-Operationen
- `GITLAB_GIT_TOKEN` (nur im Claude-Container): `read_repository` + `write_repository` für git — keine API-Calls möglich

**MCP Tool-Einschränkungen:** Der MCP-Sidecar blockiert per `GITLAB_DENIED_TOOLS_REGEX` alle schreibenden Operationen außer:
- `create_merge_request` — MR anlegen
- `create_merge_request_note` — Kommentar schreiben
- `create_merge_request_discussion_note` — Antwort in einem Thread
- `create_merge_request_thread` — neuen Diskussions-Thread starten

**Kein Root:** Claude Code läuft als unprivilegierter User `dev`. Der Entrypoint wechselt via `gosu` zu diesem User, sobald `/workspace`-Ownership gesetzt ist.

## Einrichtung

### 1. GitLab aktivieren (optional)

GitLab-Integration ist optional. Ohne sie startet nur `claude-dev-env`.

Docker Compose unterstützt *Profiles* — Services können einem benannten Profil zugeordnet werden und starten dann nur, wenn dieses Profil aktiv ist. `gitlab-mcp` gehört zum Profil `gitlab`. Aktiv wird ein Profil über die Umgebungsvariable `COMPOSE_PROFILES`:

```dotenv
# .env
COMPOSE_PROFILES=gitlab   # gitlab-mcp wird gestartet
# COMPOSE_PROFILES=        # auskommentiert → nur claude-dev-env startet
```

`claude-dev-env` hat `required: false` auf seiner Abhängigkeit zu `gitlab-mcp`. Das bedeutet: wenn `gitlab-mcp` gar nicht existiert (Profil inaktiv), startet `claude-dev-env` trotzdem. Ist das Profil aktiv, wartet `claude-dev-env` dennoch bis `gitlab-mcp` healthy ist, bevor es startet.

### 2. Tokens erstellen

**GitLab API Token** (für MCP-Sidecar):
- Group → Settings → Access Tokens
- Scopes: `api`
- In `.env` als `GITLAB_API_TOKEN` eintragen

**GitLab Git Token** (für git-Operationen im Claude-Container):
- Group → Settings → Access Tokens
- Scopes: `read_repository`, `write_repository`
- In `.env` als `GITLAB_GIT_TOKEN` eintragen

### 2. Claude-Credentials synchronisieren

Einmalig auf dem Host (Claude Code muss dort installiert und eingeloggt sein):

```bash
python3 entrypoint.py sync
```

### 3. `.env` befüllen

```bash
cp .env.example .env   # falls vorhanden, sonst direkt .env bearbeiten
```

Mindestens setzen:
```
ANTHROPIC_API_KEY=...
GITLAB_API_TOKEN=...
GITLAB_GIT_TOKEN=...
GITLAB_API_URL=https://gitlab.com/api/v4   # oder eigene Instanz
```

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
