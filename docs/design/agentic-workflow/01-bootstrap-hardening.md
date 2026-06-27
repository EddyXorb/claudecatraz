# 01 — Bootstrap-Härtung

Umgesetzt. Parallelität: gleichzeitig mit [`01-gitlab-native.md`](./01-gitlab-native.md) machbar.

---

## Was gemacht wurde

| Maßnahme | Wirkung |
| -------- | ------- |
| GitLab-Tokens aus dem Agent-Container entfernt | Kein nutzbares GitLab-Credential im Agenten — alle Tool-Sperren irrelevant, weil nichts zu umgehen bleibt. |
| `entrypoint.py`: `.netrc`-/`GITLAB_GIT_TOKEN`-Erzeugung entfernt | Kein Git-Write-Token mehr im Container. |
| Dediziertes Sandbox-Konto für Claude | „Kompromittiert" bedeutet nur leere Sandbox-Historia, keine Wirkung auf das Primärkonto. |

---

## Konfiguration

`.env` → `ANTHROPIC_API_KEY` zeigt auf das dedizierte Konto. Das gemountete `claude/`-Verzeichnis enthält nur die Sandbox-Credential, kein weiteres Konto-Material.

**Verifikation:** Im Agent-Container `printenv | grep -i gitlab` → kein Token. (Auch Teil der Red-Team-Suite A1, → [`03-testing-redteam.md`](./03-testing-redteam.md).)

---

## Offener Punkt

⚠️ **RC-Token-Fähigkeiten** (vor Inbetriebnahme klären): Kann das Claude-OAuth-Token fremde Remote-Control-Sessions enumerieren/ansteuern? Bis zur Klärung gilt: dediziertes Konto + ggf. RC-loser Betrieb. Strukturelle Prävention schlägt „wahrscheinlich nicht".
