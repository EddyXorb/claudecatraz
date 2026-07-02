# 05 — Austauschbarer Agent-Layer

Der Base-Layer ist bereits austauschbar (`BASE_IMAGE`/`BASE_DOCKERFILE`). Für den
Agent-Layer gilt nach Röst-Runde 1 eine ehrliche Neubewertung: **das Manifest ist die
letzten 10 % — die eigentliche Arbeit ist die Zerlegung des Entrypoints.**

## 05.1 Wo die Claude-Kopplung wirklich sitzt

Nicht im `claude-layer/Dockerfile` (das sind `npm install -g @anthropic-ai/claude-code` und
drei Zeilen Drumherum), sondern:

1. **`container/entrypoint.py` (~400 Zeilen)** — tmpfs-Home-Aufbau, `.credentials.json`- und
   `.claude.json`-Layout, Settings-Migrationen über Claude-Versionen
   (`skipDangerousModePermissionPrompt`), CLAUDE.md-Installation, Remote-Control-Start.
2. **Credential-Lifecycle** — `catraz sync` importiert `~/.claude/.credentials.json`
   read-only; OAuth-Refresh persistiert nicht (tmpfs, dokumentierter Re-Sync-Workaround).
   Jede Agent-CLI hat hier *andere* Semantik (API-Key-only, Token-Datei, Keychain, …).
3. **CLI-Konstanten** — Kommando `claude`, Debug-Flags, Remote-Control-Modus in
   `run.py`/`compose.py`/`_sync.py`.

## 05.2 Schnittlinie: generischer Entrypoint + Agent-Adapter als Code-Asset

Der Entrypoint zerfällt in zwei Teile mit definiertem Vertrag:

- **Generisch (ein Code, alle Agenten):** UID-Mapping, tmpfs-Home, Workspace-Setup,
  `.catraz`-Shadow-Mount-Kontrakt, Proxy-Env, Prozess-Exec und Signal-Handling.
- **Agent-Adapter (pro Agent, Code im Repo — nie aus `.catraz/` geladen, A2):**
  `prepare_home(home: Path, secrets: Secrets) -> None` (Credential-Dateien und
  Settings-Layout schreiben), `command(argv: list[str]) -> list[str]`,
  `instructions_target() -> Path` (wo AGENT.md hin muss), optional
  `remote_command() -> list[str] | None`.

Adapter sind **mitgelieferte Python-Module** (`src/catraz/assets/agents/<name>/adapter.py`),
keine Config: sie brauchen echte Logik (Settings-Migrationen!), und A2 verbietet, Logik aus
Nutzer-Config zu laden. Ein eigener Agent = ein PR bzw. ein Fork mit eigenem Adapter — das
ist die bewusste Grenze der Erweiterbarkeit, analog §04.4.

## 05.3 Das Manifest — der deklarative Rest

```
src/catraz/assets/agents/claude/
├── layer.Dockerfile        # heutiger claude-layer
├── adapter.py              # prepare_home, command, … (05.2)
└── agent.toml
```

```toml
# agent.toml — nur was wirklich deklarativ ist
name    = "claude"
command = "claude"
[credentials]
subscription_source = "~/.claude/.credentials.json"   # was `catraz sync` importiert
api_key_env         = "ANTHROPIC_API_KEY"
[modes]
remote = true            # Remote-Control ist ein Claude-Feature → pro Agent gate-n
[logs]
debug_flag = "--debug-file"
[egress]
domains = ["api.anthropic.com", "statsig.anthropic.com"]   # siehe 05.4 — NICHT auto-gemergt
```

Auswahl per `.catraz/.env`: `AGENT_PROFILE=claude` (Default). Die CLI liest Kommando,
Credential-Pfade und Debug-Flags aus dem Manifest statt aus Konstanten; `claude`-Strings
verschwinden aus dem generischen CLI-Code.

## 05.4 Egress ist die Exfiltrationsgrenze — niemals automatisch mergen

Röst-Runde 1 hat zu Recht angemerkt: `egress.domains` pro Profil automatisch in die
Squid-Allowlist zu mergen macht die *einzige* echte Exfiltrationsgrenze zu Profil-Daten —
genau der Ort, an dem sich `evil.com` einnistet. Deshalb:

- Die Domains eines Profils sind ein **Vorschlag**, den `catraz init` anzeigt und der Nutzer
  einzeln bestätigt; sie landen als markierter, kommentierter Block in der Squid-Allowlist
  (`# agent:claude`), nie unsichtbar.
- `catraz doctor` druckt die effektive Egress-Liste inklusive Herkunft jeder Domain.
- Profile außerhalb der mitgelieferten Assets (Fork/eigener Adapter) erfordern bei `init`
  eine explizite Bestätigung mit Diff der Egress-Domains.

## 05.5 Was pro Agent ehrlich offen bleibt

- **Credential-Refresh:** der Adapter deklariert, ob Refresh-Persistenz nötig ist; das
  heutige „tmpfs verwirft Refresh, `catraz sync` heilt" ist Claude-Verhalten und darf nicht
  stillschweigend auf andere Agenten übertragen werden.
- **Remote Control** ist ein Claude-Alleinstellungsmerkmal; `modes.remote=false` muss den
  `claude-remote`-Modus sauber verweigern (fail-closed statt kaputtem Daemon).
- **AGENT.md-Äquivalent:** wohin Instruktionen müssen (CLAUDE.md vs. AGENTS.md vs. nichts)
  weiß nur der Adapter.
