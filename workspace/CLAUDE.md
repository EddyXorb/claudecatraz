# Projekt-CLAUDE.md (Platzhalter)

Diese Datei ist **projektspezifisch** und gehört dem Repo, das nach `/workspace`
gemountet wird (Build-Befehle, Code-Style, Test-Kommandos dieses Projekts). Im echten
Betrieb mountet `PROJECT_DIR` den jeweiligen GitLab-Clone hierher und diese Vorlage wird
von der CLAUDE.md des Projekts ersetzt.

**Sandbox-/Harness-Wissen** (Netz, Egress, Forward-Proxy, Warden, GitLab-API) steht
NICHT hier, sondern wird vom Container aus dem Image als User-Memory injiziert
(`~/.claude/CLAUDE.md`, Quelle: `AGENT.md` im Repo-Root). So bleibt es projekt­unabhängig
und kann nicht versehentlich in ein Projekt-Repo committet werden.
