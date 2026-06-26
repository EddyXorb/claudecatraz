# claude-dev-env

Dockerisierte Claude Code Umgebung für autonome Agenten-Sessions, gesteuert über Remote Control. GitLab- und GitHub-Integration laufen als optionale MCP-Sidecar.

## Architektur

```
Host (VSCode / Browser)
        │  Remote Control (claude.ai)
        ▼
┌─────────────────────┐     ┌──────────────────────┐
│   claude-dev-env    │─MCP▶│     gitlab-mcp       │──▶ gitlab.com API
│                     │     │  GITLAB_API_TOKEN    │
│  Claude Code        │     └──────────────────────┘
│  + Toolchain        │
│  (C++/Rust/Python)  │─MCP▶┌──────────────────────┐
│                     │     │     github-mcp       │──▶ api.github.com
└─────────────────────┘     │  GITHUB_TOKEN        │
        │                   └──────────────────────┘
        │ git over HTTPS
        ▼ (GITLAB_GIT_TOKEN, read/write_repository)
   gitlab.com
```

Der Agent hat keinen direkten Zugriff auf GitHub-Tokens, aber leider schon auf git gitlab tokens (das ging nicht anders). Alle API-Operationen laufen über die jeweiligen MCP-Sidecar.

## Dateien

| Datei                | Zweck                                                                          |
| -------------------- | ------------------------------------------------------------------------------ |
| `docker-compose.yml` | Definiert alle Services und ihre Umgebungsvariablen                            |
| `Dockerfile`         | Build-Image: Ubuntu 24.04 mit Clang, Rust, Python/uv, Conan, Node, Claude Code |
| `entrypoint.py`      | Startet den Container; konfiguriert git-Credentials und MCP-Verbindungen       |
| `.env`               | Tokens und Konfiguration (nicht committen)                                     |
| `claude/`            | Persistierter Claude-Home (`~/.claude` im Container, bind-gemountet)           |
| `workspace/`         | Arbeitsverzeichnis für Projekte (bind-gemountet)                               |

## Sicherheitsmaßnahmen

**Token-Trennung:** Separate Tokens mit minimalen Scopes.

- `GITLAB_API_TOKEN` (nur im `gitlab-mcp`-Container): `api`-Scope für GitLab-API-Operationen
- `GITLAB_GIT_TOKEN` (nur im `claude-dev-env`-Container): `read_repository` + `write_repository` für git — keine API-Calls möglich
- `GITHUB_TOKEN` (nur im `github-mcp`-Container): Claude sieht ihn nie

**MCP Tool-Einschränkungen (GitLab):** Der `gitlab-mcp`-Sidecar blockiert per `GITLAB_DENIED_TOOLS_REGEX`:

- alle `delete_*`-Tools
- weitere destruktive Einzeloperationen (`update_default_branch`, `unprotect_branch`, `create_or_update_file`, `merge_merge_request`)

**Kein Root:** Claude Code läuft als unprivilegierter User `dev`. Der Entrypoint wechselt via `gosu` zu diesem User, sobald `/workspace`-Ownership gesetzt ist.

**Egress-Isolation (Forward-Proxy):** Der Agent hängt nur am `agent-net` (`internal: true`) — also **keine eigene Internet- oder DNS-Route**. Sein einziger Research-/Build-Pfad nach außen ist der `forward-proxy` (Squid), der gegen eine **Domain-Allowlist** filtert (default-deny). HTTPS wird per **SNI-peek + splice** gefiltert, *nicht* entschlüsselt — kein CA im Agenten. Der Proxy hält **keine** Credentials; selbst kompromittiert kann der Agent nur zu allowlisteten Zielen sprechen. Jede Verbindung landet im Audit-Log `logs/squid/access.log`. Umsetzung des Plans [`docs/design/agentic-workflow/02-forward-proxy/`](docs/design/agentic-workflow/02-forward-proxy.md).

- **Allowlist pflegen:** `config/allowlist.txt` (eine Domain je Zeile, `.domain` = inkl. Subdomains). Reload ohne Neustart: `docker compose exec forward-proxy squid -k reconfigure`.
- **Squid-Config:** `config/squid.conf` (host-editierbar, read-only gemountet — **keine Secrets**).
- **Egress prüfen:** `grep <ziel> logs/squid/access.log`.
- **Interim:** Bis der Warden (`02-warden.md`) existiert, laufen die GitLab/GitHub-MCP-Sidecars über `egress-net` (im `no_proxy` des Agenten) und der direkte `git`-Push zu `gitlab.com` über den Proxy (daher in der Allowlist).

## Einrichtung

### 1. Compose-Profile wählen

Die GitLab- und GitHub-Integration sind optional. Docker Compose *Profile* steuern, welche Sidecar-Services starten. Aktiv werden Profile über `COMPOSE_PROFILES` in `.env`:

| Wert            | Was startet          |
| --------------- | -------------------- |
| *(leer)*        | nur `claude-dev-env` |
| `gitlab`        | + `gitlab-mcp`       |
| `github`        | + `github-mcp`       |
| `gitlab,github` | alle drei            |

`claude-dev-env` hat `required: false` auf beiden Abhängigkeiten — es startet immer, wartet aber auf einen aktiven Sidecar, bis dieser healthy ist.

### 2. Tokens erstellen

**GitLab API Token** (für `gitlab-mcp`):

- Group → Settings → Access Tokens
- Scopes: `api`
- In `.env` als `GITLAB_API_TOKEN`

**GitLab Git Token** (für git-Operationen im `claude-dev-env`):

- Group → Settings → Access Tokens
- Scopes: `read_repository`, `write_repository`
- In `.env` als `GITLAB_GIT_TOKEN`

**GitHub Token** (für `github-mcp`):

- GitHub → Settings → Developer settings → Personal access tokens
- Fine-Grained Token empfohlen; Scopes je nach Bedarf
- In `.env` als `GITHUB_TOKEN`

### 3. Claude-Credentials synchronisieren

Einmalig auf dem Host (Claude Code muss dort installiert und eingeloggt sein):

```bash
python3 entrypoint.py sync
```

### 4. `.env` befüllen

Mindestens setzen:
```
ANTHROPIC_API_KEY=...
COMPOSE_PROFILES=gitlab,github   # oder leer lassen

GITLAB_API_URL=https://gitlab.com/api/v4
GITLAB_API_TOKEN=...
GITLAB_GIT_TOKEN=...

GITHUB_TOKEN=...
```

### 5. Log-Verzeichnis anlegen

Der Forward-Proxy schreibt sein Audit-Log in den Bind-Mount `logs/squid/` — der Ordner muss vor dem Start existieren (ist im Repo via `.gitkeep` vorhanden):

```bash
mkdir -p logs/squid
```

### 6. Starten

```bash
docker compose up -d
```

Der Agent ist danach über Remote Control unter claude.ai erreichbar.

**Egress testen** (Allowlist hält, Rest geblockt — Red-Team A11):

```bash
tests/redteam/test_egress.sh
```

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
