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

**Egress-Isolation (Forward-Proxy):** Der Agent hängt nur am `agent-net` (`internal: true`) — also **keine eigene Internet- oder DNS-Route**. Sein einziger Research-/Build-Pfad nach außen ist der `forward-proxy` (Squid), der gegen eine **Domain-Allowlist** filtert (default-deny). HTTPS wird per **SNI-peek + splice** gefiltert, *nicht* entschlüsselt — kein CA im Agenten. Der Proxy hält **keine** Credentials; selbst kompromittiert kann der Agent nur zu allowlisteten Zielen sprechen. Jede Verbindung landet im Audit-Log `logs/squid/access.log`. Umsetzung des Plans [`docs/design/agentic-workflow/02-forward-proxy/`](docs/design/agentic-workflow/02-forward-proxy.md).

- **Allowlist pflegen:** `config/allowlist.txt` (eine Domain je Zeile, `.domain` = inkl. Subdomains). Reload ohne Neustart: `docker compose exec forward-proxy squid -k reconfigure`.
- **Squid-Config:** `config/squid.conf` (host-editierbar, read-only gemountet — **keine Secrets**).
- **Egress prüfen:** `grep <ziel> logs/squid/access.log`.
- **GitLab-Entscheidungen prüfen:** Warden-Audit-Viewer im Browser unter **<http://172.31.0.2:9090/>** (Admin-Port, nur vom Host) — siehe [🔍 Audit-Log im Browser ansehen](#-audit-log-im-browser-ansehen).
- **Interim:** Bis der Warden (`02-warden.md`) existiert, laufen die GitLab/GitHub-MCP-Sidecars über `egress-net` (im `no_proxy` des Agenten) und der direkte `git`-Push zu `gitlab.com` über den Proxy (daher in der Allowlist).

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

### 3.1 Log-Verzeichnis anlegen

Der Forward-Proxy schreibt sein Audit-Log in den Bind-Mount `logs/squid/` — der Ordner muss vor dem Start existieren (ist im Repo via `.gitkeep` vorhanden):

```bash
mkdir -p logs/squid
```

### 4. Starten

In **Stufe 01 hält der Agent kein GitLab-Token** — es sind keine GitLab-/GitHub-Tokens
einzutragen. GitLab-Zugriff (und die zugehörigen Read-/Write-Tokens, die dann nur der
**Warden** bekommt) folgt in Stufe 02; siehe `docs/design/agentic-workflow/` §11.

```bash
docker compose up -d
```

Der Agent ist danach über Remote Control unter claude.ai erreichbar.

> ### 🔍 Audit-Log im Browser ansehen
>
> **Viewer-URL: <http://172.31.0.2:9090/>**
>
> Der **Warden** (ab Stufe 02) serviert ein read-only Web-UI über **jede** GitLab-Entscheidung
> (allow/deny mit Regel R1–R6, R4/R5 hervorgehoben) — gefiltert nach Kanal/Entscheidung/Regel/Projekt.
>
> - Die IP **`172.31.0.2`** ist im Compose fest verdrahtet (`ADMIN_HOST` + `admin-net`-`ipv4_address`)
>   und ändert sich nie. Port **9090**.
> - Erreichbar **nur vom Host**, **nicht** vom Agenten — eigenes `admin-net`, bewusst ohne
>   veröffentlichten Host-Port (W3). Deshalb geht `localhost:9090` *nicht*.
> - localhost gewünscht? Loopback-Tunnel auf dem Host:
>   `socat TCP-LISTEN:9090,bind=127.0.0.1,reuseaddr,fork TCP:172.31.0.2:9090`
> - Rohes JSONL: <http://172.31.0.2:9090/audit> · Health: <http://172.31.0.2:9090/healthz>

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
