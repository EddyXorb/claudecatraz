# 05 — Austauschbarer Agent-Layer

> **Umgesetzt in Schritt 7** (`docs/design/architecture-generalization/06-migration.md`):
> `src/catraz/assets/container/{entrypoint.py,agent_contract.py,git_routing.py}` +
> `src/catraz/assets/agents/claude/` + `src/catraz/agents.py`. Diese Datei bleibt die
> Spezifikation; Umsetzungsdetails und bewusste Abweichungen stehen im
> Umsetzungsvermerk zu Schritt 7.

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
  `environ(secrets: Secrets) -> dict[str, str]` (viele CLIs lesen Key *und* Base-URL/Org
  aus Env — ein einzelnes `api_key_env` reicht nicht, Röst-Runde 2),
  `render_instructions(ctx: InstructionContext) -> tuple[Path, str]` (Ziel **und Inhalt**:
  die heutige `AGENT.md` ist nicht agent-neutral — Namespace-Präfix, Warden-REST-Basis-URL
  und curl-Beispiele müssen pro Agent gerendert werden, nicht nur platziert), optional
  `remote_command() -> list[str] | None`. Der `InstructionContext` trägt u.a. die
  Forge-REST-Basis (`http://gitlab-warden:8080/api/v4`) als expliziten Input — der
  REST-Draht des Agenten ist Teil des Vertrags, nicht Prosa-Zufall.

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
subscription_source = "~/.claude/.credentials.json"   # was `catraz sync` importiert (mode="sync")
api_key_env         = "ANTHROPIC_API_KEY"
mode                = "persistent"   # "sync" | "persistent" — Maintainer-Entscheid, siehe 05.6
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

## 05.5 Adapter-Conformance-Harness — macht A11 überprüfbar

*Aus Röst-Runde 2 (Roaster-Idee I3).* Ein Protokoll allein prüft nichts. Eine ausführbare
Harness fährt pro Adapter den Container hoch und assertet den Sicherheitskontrakt:

- kein Forge-Credential und kein fremdes Modell-Credential im Agent-Prozess/-Home,
- Egress zu nicht-allowlisteten Domains geblockt,
- git-`insteadOf` zeigt auf den Warden (kein direkter Forge-Weg),
- `modes.remote=false` verweigert den Remote-Modus sauber (fail-closed statt kaputtem Daemon).

Damit wird „der Agent ist untrusted Nutzlast" (A11) für jeden neuen Adapter und jeden Fork
ein rot/grün-Signal statt eines Versprechens. Sie erweitert die bestehende
Red-Team-Suite (`tests/redteam/`) um die Agent-Dimension.

## 05.6 Was pro Agent ehrlich offen bleibt

- **Credential-Refresh (Maintainer-Entscheid, 2026-07):** der Adapter deklariert seinen
  Credential-Modus im Manifest (`credentials.mode`), mit zwei Ausprägungen:
  - `sync` (heutiges Verhalten): `catraz sync` importiert die Host-Credentials read-only,
    Refresh landet im tmpfs und stirbt mit dem Container. Nur für Kurzläufer tragfähig —
    mit bekanntem Defekt: rotierende OAuth-Refresh-Tokens bilden mit dem Host **eine**
    Token-Familie; wer zuerst refresht (Host oder Container), invalidiert die andere
    Seite. Das erklärt die zuverlässigen `claude-remote`-Abbrüche nach wenigen Stunden.
  - `persistent` (Default für Langläufer/`claude-remote`): einmaliges, **eigenes**
    `claude login` im Dev-Container (Paste-URL-Flow) statt geteilter Token-Familie; der
    Claude-State lebt in `.catraz/state/claude/` (0700) und wird writable in jeden
    Dev-Container des Repos gemountet → persistenter State pro Catraz-Repo, Refresh
    überlebt Container-Neustarts. **Selektiv, nie das ganze Home:** persistiert werden
    Credentials (`.credentials.json`) und Session-/Projekt-State; Settings/Hooks/
    Slash-Commands baut der Entrypoint weiterhin aus dem Image auf — ein voll-persistentes
    `~/.claude` wäre eine Selbst-Reinfektions-Fläche (A11: eine kompromittierte Session
    schriebe sich einen Hook, der jede künftige Session infiziert, über Neustarts hinweg).
  - **Nebenläufigkeit:** mehrere Container auf demselben State-Ordner sind dieselbe
    Situation wie mehrere Claude-Instanzen auf einem Host — dateibasierte Koordination
    (flock wirkt über bind-mounts, ein Kernel; atomare renames), Session-Dateien pro UUID.
    Rest-Risiko ist der gleichzeitige Token-Refresh zweier Container (Sekunden-Fenster,
    alle paar Stunden) — akzeptiert; bei Bedarf später `--claude-state <name>` pro Instanz.
  - `catraz doctor` prüft Mode-Konsistenz (persistenter Ordner vorhanden/Permissions);
    eine CLI-Option unterbindet den Sync für `mode = "persistent"`-Läufe.
- **Remote Control** ist ein Claude-Alleinstellungsmerkmal; `modes.remote=false` muss den
  `claude-remote`-Modus sauber verweigern (fail-closed statt kaputtem Daemon).
- **AGENT.md-Äquivalent:** wohin Instruktionen müssen (CLAUDE.md vs. AGENTS.md vs. nichts)
  weiß nur der Adapter.
