# 01 — Bootstrap-Härtung (Sofortmaßnahmen)

Die **kostenlosen, sofort umsetzbaren** Schritte, die die akute R6-Verletzung schließen
und den Blast-Radius des Claude-Kontos verkleinern — **bevor** irgendeine Zeile Warden
gebaut wird. Setzt [`README.md`](./README.md) §4 (Bestandsaufnahme), §3.2 (Claude-
Credentials) und Roadmap §10 Schritte 1 + 1b um.

Status: **Implementierungs-Entwurf.** Reines Config-/Konto-Handling, kein Code.
Querverweise „§x" → README.

**Parallelität:** Trägt das Präfix `01` gemeinsam mit
[`01-gitlab-native.md`](./01-gitlab-native.md). Beide sind voneinander unabhängig (lokale
Container-/Konto-Config hier vs. GitLab-Web-Config dort) und können **gleichzeitig**
erledigt werden. Beide sind Voraussetzung für die `02`-Pläne (Warden, Forward-Proxy).

---

## 1B.1 Ziel & Wirkung

| Maßnahme | Behebt | Wirkung |
| -------- | ------ | ------- |
| GitLab-Tokens aus dem Agent-Container ziehen | §4 / R6 | Kein nutzbares GitLab-Credential mehr im Agenten — MCP-Tool-Sperren werden irrelevant, weil es nichts mehr zu umgehen gibt. |
| Dediziertes Wegwerf-Claude-Konto | §3.2 | „Kompromittiert" bedeutet leere Historie, nur Sandbox-RC-Sessions, separates Budget. Wichtigste Einzelmaßnahme gegen Credential-Diebstahl. |

Beides ist **unabhängig vom Warden** und reduziert das Risiko sofort. Nach diesem Plan
hat der Agent zwar (bis `02`) noch keinen sauberen GitLab-Pfad — aber auch **keinen
gefährlichen** mehr.

---

## 1B.2 GitLab-Token-Leak schließen

**Befund (§4):** `docker-compose.yml` reicht `GITLAB_API_TOKEN` an `claude-dev-env`
durch (Zeile 52), und ein `GITLAB_GIT_TOKEN` mit `write_repository` liegt im Agenten
(`.netrc`). Beide sind aus dem Agenten lesbar → alle Tool-Sperren wirkungslos.

**Änderungen am bestehenden `docker-compose.yml`:**

```yaml
  claude-dev-env:
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      # ENTFERNT: - GITLAB_API_TOKEN=${GITLAB_API_TOKEN:-}
      # ENTFERNT: - GITLAB_API_URL=...   (kommt in 02 als Warden-URL zurück)
```

- `GITLAB_API_TOKEN` aus dem `environment:`-Block des **Agenten** streichen.
- In `entrypoint.py`: die `.netrc`-/`GITLAB_GIT_TOKEN`-Erzeugung für den Agenten
  entfernen (kein Git-Write-Token mehr im Container).
- Der `gitlab-mcp`-Sidecar (der den Token bisher hielt) wird in `02` ohnehin durch den
  Warden ersetzt; bis dahin kann er deaktiviert bleiben (`COMPOSE_PROFILES` ohne
  `gitlab`), denn ohne sauberen Pfad soll der Agent vorübergehend keinen GitLab-Zugriff
  haben (fail-closed ist die sichere Zwischenstufe).

**Verifikation:** Im Agent-Container `printenv | grep -i gitlab` und ein Scan nach
`.netrc` → **kein** echtes Token auffindbar. (Wird später Teil der Red-Team-Suite,
[`03-testing-redteam.md`](./03-testing-redteam.md).)

---

## 1B.3 Dediziertes Claude-Konto (§3.2)

**Grundsatz:** Die Sandbox läuft **niemals** mit dem persönlichen/Org-Primärkonto. Das
ist unter der adversarischen Prämisse keine vertretbare Default-Konfiguration.

Gestaffelt, stärkste Maßnahme zuerst:

1. **Wegwerf-/Service-Konto anlegen**, das für **nichts** anderes verwendet wird (nie auf
   Laptop/Primär-Claude eingeloggt, steuert keine anderen Agenten). Damit sind die
   *einzigen* RC-Sessions dieses Kontos die der Sandbox → „Self-Hijack" ist bedeutungslos,
   laterale Bewegung gibt es nicht (§3.2 RC-Hijack).
2. **Ein Konto pro Sandbox-Instanz**, falls mehrere Sandboxen unterschiedliche
   Trust-Niveaus haben → eine kompromittierte Sandbox kann keine andere übernehmen.
3. **Budget-/Scope-Grenze:** wo möglich budget-gedeckelter API-Key statt Abo-Token —
   harte Kostenobergrenze, trivial revozierbar. Trade-off: ggf. kein Remote Control.
4. **Remote Control weglassen, wo nicht zwingend gebraucht** — RC ist das Feature, das
   fernsteuerbare Sessions erst erzeugt; ein headless/API-Key-Betrieb hat keine
   RC-Sessions zum Kapern (§3.2).
5. **Mount-Hygiene:** nur `~/.claude/.credentials.json` mounten, kein weiteres
   Konto-Material; Bind-Mount so eng wie möglich.

**Konfiguration:** `.env` → `ANTHROPIC_API_KEY` bzw. `CLAUDE_HOME` auf das dedizierte
Konto zeigen lassen; sicherstellen, dass das gemountete `claude/`-Verzeichnis nur die
Sandbox-Credential enthält.

---

## 1B.4 Offene Verifikationspunkte (vor Inbetriebnahme)

- ⚠️ **RC-Token-Fähigkeiten** (§3.2 / §9): Kann das Claude-OAuth-Token fremde
  Remote-Control-Sessions enumerieren/ansteuern? Bis zur Klärung gilt die strukturelle
  Prävention (dediziertes Konto + ggf. RC-loser Betrieb) als verbindlich, nicht als
  „wahrscheinlich nicht".
- ⚠️ Ob ein **Auth-injizierender Modell-Proxy** (analog Warden) mit RC/OAuth überhaupt
  möglich ist oder nur mit API-Key (§3.2) — relevant, falls man die Claude-Auth ebenfalls
  aus dem Agenten ziehen will.

---

## 1B.5 Definition of Done

- [ ] Kein `GITLAB_API_TOKEN`/`GITLAB_GIT_TOKEN` mehr im Agent-Container (verifiziert per
      `printenv` + FS-Scan).
- [ ] `entrypoint.py` erzeugt kein Git-Write-Token/`.netrc` mit echtem Token mehr.
- [ ] Sandbox läuft auf einem **dedizierten** Claude-Konto, nicht dem Primärkonto.
- [ ] Mount enthält nur die Sandbox-Credential.
- [ ] RC-Verifikationspunkt dokumentiert/entschieden.
