# `config/` — host-editierbare Konfiguration (read-only gemountet)

Dieser Ordner enthält die **zu konfigurierenden, nicht-geheimen** Dateien der Sandbox. Er
wird **read-only** in die Container gemountet und ist bewusst **vom Host aus editierbar**
(z. B. in VSCode), damit Policy/Allowlist ohne Image-Rebuild gepflegt werden können.

**Grundregel: hier liegt NIE ein Geheimnis.** Tokens/Secrets gehören ausschließlich in
`.env` (gitignored) und gehen nur an den jeweils berechtigten Service. Begründung und
Gesamtbild: `docs/design/agentic-workflow/README.md` §11.

| Datei | Für | Wirkt ab | Doku |
| ----- | --- | -------- | ---- |
| `allowlist.txt` | Forward-Proxy (Squid): erlaubte Domains | Stufe 02 | `02-forward-proxy/03-squid-config.md` |
| `squid.conf` | Forward-Proxy (Squid): Filter-Konfiguration | Stufe 02 | `02-forward-proxy/03-squid-config.md` |
| `warden.toml` | Warden: Präfix, Limits, erlaubte Projekte | Stufe 02 | `02-warden.md` (W10) |

**Versionierung:** `config/` wird **bewusst committet** (es ist das Policy-Artefakt). Im
Gegensatz dazu sind `.env` (Secrets) sowie `state/` und `logs/` (Laufzeitdaten)
gitignored.

**Status:** Stufe 01 (Bootstrap-Härtung) legt diese Dateien als Gerüst an; sie werden erst
mit den Stufe-02-Containern (Warden, Forward-Proxy) wirksam.
