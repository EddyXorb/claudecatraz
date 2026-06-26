# Agentic Workflow — GitLab-Sandbox für Claude

Designdokument für ein „Claude-Gefängnis" in Docker, aus dem heraus Claude GitLab
benutzen kann wie über ein MCP — aber unter scharfen, nicht umgehbaren Restriktionen.

Status: **Entwurf / Planung.** Dieses Dokument legt die Regeln, das Bedrohungsmodell
und mehrere Architekturoptionen fest. Es ist noch keine Implementierung.

---

## 1. Ziel

Claude läuft autonom in einem Container und soll an GitLab (Premium, gitlab.com)
arbeiten dürfen: lesen, Branches mit definiertem Präfix pushen, Merge Requests dazu
anlegen und pflegen, CI triggern. Alles andere ist verboten. Die Restriktionen müssen
auch dann halten, wenn der Agent **böswillig** ist und aktiv versucht, sie zu umgehen.

---

## 2. Regeln (verbindlich)

| #      | Regel                                                                                                                                                                                                  |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **R1** | **Lesen erlaubt auf alles**, was die GitLab-API für MRs, CI und Repos hergibt: Dateien, Branches, Commits, Pipelines, Jobs, Logs, MRs, Kommentare, etc.                                                  |
| **R2** | **Ausnahme zu R1 (Schreiben):** Claude darf auf Branches pushen, deren Name mit einem **frei konfigurierbaren Präfix** beginnt (z. B. `claude/`). Sonst auf keinen Branch.                               |
| **R3** | **Ausnahme zu R1 (Schreiben):** Für genau die unter R2 erlaubten Branches darf Claude MRs **erstellen**, Kommentare **beantworten/schreiben**, die **CI triggern** und den **MR bearbeiten** (inkl. wieder schließen). Nur für diese MRs. |
| **R4** | MRs dürfen von Claude **niemals gemerged** werden können.                                                                                                                                               |
| **R5** | Frei konfigurierbare Obergrenzen gegen DoS/Chaos: **max. gleichzeitig offene MRs** (von Claude erstellt), **max. gleichzeitig existierende Claude-Branches**, **max. schreibende Aktionen pro Stunde** (Kommentare, CI-Trigger, Pushes …). |
| **R6** | Alle Regeln sind **architektonisch** so umzusetzen, dass Claude sie selbst mit Tricks nicht umgehen kann. Konfigurations-Flags eines Tools, dem der Agent vertraut werden müsste, genügen **nicht**.    |

Konfigurierbare Parameter (Default-Vorschläge):

```
BRANCH_PREFIX           = "claude/"      # R2
MAX_OPEN_MRS            = 5              # R5
MAX_OPEN_BRANCHES       = 10             # R5
MAX_WRITES_PER_HOUR     = 60            # R5
```

---

## 3. Bedrohungsmodell

**Annahme (Worst Case):** Der Agent ist vollständig kompromittiert oder böswillig. Er
kann beliebige Prozesse im Container starten, das Dateisystem lesen, Umgebungsvariablen
auslesen, beliebigen Netzwerkverkehr erzeugen, Tool-Definitionen ignorieren und rohe
HTTP-/git-Requests senden.

**Daraus folgt das Kernprinzip für R6:**

> **Jedes Credential, das im Prozessraum des Agenten liegt (Env-Var, Datei, Argument),
> gilt als kompromittiert.** Ein Token mit `api`-Scope im Agent-Container ist
> gleichbedeutend mit „der Agent darf alles, was das Token darf" — unabhängig davon,
> welche MCP-Tools wir freigeben oder sperren.

Konsequenzen:

1. **Im Agent-Container darf kein GitLab-Token mit mehr Rechten liegen, als die Policy
   erlaubt.** Idealerweise gar kein GitLab-Token.
2. Die Policy muss an einer **Vertrauensgrenze außerhalb** des Agenten durchgesetzt
   werden (eigener Prozess/Container, den der Agent nicht steuert).
3. Der einzige Netzwerkweg des Agenten zu GitLab führt **durch** diese Vertrauensgrenze
   (Egress-Isolation). Direkter Zugriff auf `gitlab.com` wird unterbunden.
4. Tool-Filter im MCP sind **Bequemlichkeit/Defense-in-Depth**, nicht die
   Sicherheitsgrenze.

---

## 4. Warum die aktuelle Implementierung R6 verletzt

Bestandsaufnahme des jetzigen Repos (`docker-compose.yml`, `entrypoint.py`):

- **`GITLAB_API_TOKEN` liegt im Agent-Container.** `docker-compose.yml` reicht ihn an
  `claude-dev-env` durch (env), und `entrypoint.py:configure_gitlab()` registriert das
  MCP mit `Authorization: Bearer <token>`. → Der Agent kann den Token aus seiner Env
  lesen (`printenv`) und `gitlab.com/api/v4` direkt ansprechen. **Alle MCP-Tool-Sperren
  (`GITLAB_DENIED_TOOLS_REGEX`, `GITLAB_READ_ONLY_MODE`) sind damit wirkungslos.**
- **`GITLAB_GIT_TOKEN` mit `write_repository` liegt im Agent-Container** (`.netrc`). Er
  erlaubt Push auf **jeden** Branch jedes erreichbaren Projekts — kein Präfix-Zwang,
  kein Quoten-/Rate-Limit. Verletzt R2 und R5.
- Die Restriktionen sind reine **Config eines Tools, dem der Agent vertraut werden
  müsste** → genau das, was R6 ausschließt.

Dieses Dokument beschreibt, wie man das behebt.

---

## 5. Architektur-Optionen

Vier Bauweisen, bewertet gegen die Regeln. Legende: ✅ erfüllt strukturell · ⚠️ nur per
Tool-Config (umgehbar) · ❌ nicht erfüllt.

### Option A — Nur MCP-Tool-Restriktionen (Status quo, erweitert)

Den `gitlab-mcp`-Sidecar weiter mit `DENIED_TOOLS_REGEX`/Read-Only-Flags härten, Token
bleibt aber erreichbar.

| R1 | R2 | R3 | R4 | R5 | R6 |
| -- | -- | -- | -- | -- | -- |
| ✅ | ⚠️ | ⚠️ | ⚠️ | ❌ | ❌ |

**Verworfen.** Solange ein nutzbares Token im Agent-Container liegt, ist alles
umgehbar. Rate-Limits/Quoten kennt das MCP gar nicht.

### Option B — Egress-Firewall + Token nur im MCP-Sidecar

Token aus dem Agent-Container entfernen; nur der MCP-Sidecar hält ihn. Agent erreicht
per Netzwerk-Policy ausschließlich den Sidecar, nicht `gitlab.com`. Enforcement bleibt
aber die **interne Tool-Logik** des fremden MCP.

| R1 | R2 | R3 | R4 | R5 | R6 |
| -- | -- | -- | -- | -- | -- |
| ✅ | ⚠️ | ⚠️ | ✅* | ❌ | ⚠️ |

*Merge nur, wenn `merge_merge_request` zuverlässig gesperrt ist. **Schwäche:** git-Push
ist damit nicht abgedeckt (das fremde MCP macht keine git-Pushes — die laufen weiter
über ein Git-Token), und R2/R3-Ownership-Logik und R5-Quoten kann das Standard-MCP
nicht. Brauchbar als Teilbaustein, nicht als Ganzes.

### Option C — Policy-Enforcement-Proxy („GitLab-Warden") · **EMPFEHLUNG**

Ein selbstgebauter, schlanker Vermittler-Container hält **alle** GitLab-Credentials und
ist der **einzige** Egress-Punkt des Agenten. Er setzt jede Regel selbst durch:

- **API-Schreibpfad:** filternder Reverse-Proxy vor `gitlab.com/api/v4` —
  Pfad/Methoden-Allowlist, MR-Ownership-Checks, Merge-Block, Quoten, Rate-Limit.
- **Git-Schreibpfad:** lokales bare-Repo mit `pre-receive`-Hook, der Präfix, Branch-Zahl
  und Rate prüft und nur erlaubte Refs an `gitlab.com` weiterleitet.
- **Lesepfad:** API-GETs und git-fetch werden mit einem **read-only**-Token
  durchgereicht.

Der Agent hält **kein** GitLab-Token. Das MCP des Agenten zeigt auf den Warden, nicht
auf gitlab.com; selbst wenn der Agent das MCP umgeht, kommt er per Netz nur bis zum
Warden.

| R1 | R2 | R3 | R4 | R5 | R6 |
| -- | -- | -- | -- | -- | -- |
| ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

**Empfohlen.** Details in §6.

### Option D — API-only-Schreibmodell (kein git-Push, Commit-MCP)

Wie C, aber Schreiben erfolgt **ausschließlich** über die GitLab-API
(`POST .../repository/commits`) statt git-Protokoll. Der Warden braucht dann keinen
git-Smart-HTTP-Pfad — nur den API-Filter. Einfacher abzusichern (eine einzige
Protokoll-Oberfläche), aber unbequemer für den Agenten (kein `git push`, große Diffs
müssen als API-Payload gebaut werden, kein lokaler git-Workflow für Writes).

| R1 | R2 | R3 | R4 | R5 | R6 |
| -- | -- | -- | -- | -- | -- |
| ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

**Fallback-Option**, falls der git-Proxy-Pfad zu aufwendig wird. Kombinierbar: Lesen per
git-fetch (Option C-Lesepfad), Schreiben per Commit-API (Option D).

---

## 6. Empfohlene Architektur im Detail (Option C — Warden)

### 6.1 Netz- und Container-Topologie

```
┌──────────────────────────────────────────────────────────────────┐
│ docker network: agent-net  (internal: true → KEIN Internet)        │
│                                                                    │
│   ┌────────────────────┐         ┌──────────────────────────────┐ │
│   │  claude-dev-env     │  HTTP   │        gitlab-warden          │ │
│   │  (Agent)            │────────▶│  - API-Filter (Reverse-Proxy) │ │
│   │  KEIN GitLab-Token  │  git    │  - git bare repo + pre-receive│ │
│   │  remote → warden    │────────▶│  - Quoten-/Rate-State (SQLite)│ │
│   │  MCP → warden       │         │  - hält ALLE GitLab-Tokens    │ │
│   └────────────────────┘         └───────────────┬──────────────┘ │
│                                                   │                │
└───────────────────────────────────────────────────┼────────────────┘
                                                    │ nur der Warden
                            egress-net (Internet)   │ darf raus
                                                    ▼
                                              gitlab.com / api/v4
```

Schlüsselmaßnahmen:

- **`agent-net` ist `internal: true`** → der Agent-Container hat keinerlei
  Internet-Route. Sein einziger Nachbar ist der Warden.
- Der **Warden** hängt zusätzlich an einem zweiten Netz mit Internet-Egress. Optional
  über die Firewall auf `gitlab.com` allowlisten.
- Im Agent-Container: **kein** `GITLAB_API_TOKEN`, **kein** `GITLAB_GIT_TOKEN`. `.netrc`
  und MCP-Auth zeigen auf den Warden (Dummy- oder kurzlebiges Session-Credential, das
  nur intern gilt).
- Agent läuft als non-root (wie heute), aber das ist hier zweitrangig — die Grenze ist
  das Netz, nicht der User.

### 6.2 Git-Schreibpfad (Präfix + Quoten serverseitig)

1. Agents `git remote origin` = `http://gitlab-warden/git/<project>.git`.
2. Der Warden hält je Projekt ein **bare Mirror-Repo**. Push des Agenten landet dort.
3. Ein **`pre-receive`-Hook** (läuft im Warden, nicht beim Agenten) bekommt alle
   Ref-Updates `<old> <new> <refname>` und prüft:
   - Branchname beginnt mit `BRANCH_PREFIX` → sonst **reject** (R2).
   - Löschen/Force-Push von Nicht-Claude-Refs → reject.
   - Anzahl existierender Claude-Branches < `MAX_OPEN_BRANCHES` (R5).
   - Rate-Limit `MAX_WRITES_PER_HOUR` nicht überschritten (R5).
4. Bei Annahme pusht ein Forwarder die **exakt validierten Refs** mit dem
   Write-Token an `gitlab.com`. Der Agent sieht dieses Token nie.

Warum so: Ein `pre-receive`-Hook ist die kanonische, kampferprobte Stelle für
serverseitige Push-Policy. Der Agent kann ihn nicht umgehen, weil er nur mit dem
Mirror spricht und kein gitlab.com-Token besitzt.

### 6.3 API-Schreibpfad (filternder Reverse-Proxy)

Der Warden terminiert die GitLab-REST-API für den Agenten und entscheidet pro Request:

- **GET / read** → durchreichen mit **Read-Token** (R1).
- **Schreib-Endpunkte** nur, wenn sie unter R3 fallen, und mit **Ownership-Prüfung:**
  - MR-Erstellung nur, wenn `source_branch` mit `BRANCH_PREFIX` beginnt (R2/R3).
  - Kommentare/Notes, MR-Update, CI-Trigger nur auf MRs/Pipelines, deren Source-Branch
    das Präfix trägt **und** vom Claude-Service-Account stammt (R3).
  - **Merge-Endpunkte** (`PUT .../merge`, `merge_when_pipeline_succeeds`, Accept) →
    **immer 403** (R4).
  - Alles nicht explizit Erlaubte → default-deny.
- **Quoten/Rate** vor jeder Schreibaktion gegen den State prüfen (R5):
  - offene, von Claude erstellte MRs < `MAX_OPEN_MRS`,
  - schreibende Aktionen der letzten 60 min < `MAX_WRITES_PER_HOUR`.

Der vorhandene `zereight050/gitlab-mcp`-Sidecar bleibt nutzbar — aber seine
`GITLAB_API_URL` zeigt auf den **Warden** statt auf gitlab.com, und er hält **kein**
echtes Token (nur eines für den Warden). So bleibt der bequeme MCP-Tool-Layer erhalten,
ohne Sicherheitsgrenze zu sein.

### 6.4 Zustandshaltung für Quoten (R5)

Der Warden führt eine kleine Datenbank (z. B. SQLite im Warden-Volume):

- Tabelle der vom Agenten erzeugten Branches/MRs (zum Zählen, ohne gitlab.com zu
  fluten — periodischer Reconcile gegen die API).
- gleitendes Fenster der Schreibaktionen pro Stunde (Token-Bucket).

Die Wahrheit über „offene MRs/Branches" lässt sich zusätzlich live aus der API
verifizieren (Reconcile), damit manuell geschlossene MRs das Kontingent freigeben.

### 6.5 Konfiguration

Alle Parameter aus §2 als Env des **Warden** (nicht des Agenten):

```
BRANCH_PREFIX=claude/
MAX_OPEN_MRS=5
MAX_OPEN_BRANCHES=10
MAX_WRITES_PER_HOUR=60
GITLAB_READ_TOKEN=...     # read_api, read_repository
GITLAB_WRITE_TOKEN=...    # api (eng über Account-Rolle, siehe §7)
GITLAB_API_URL=https://gitlab.com/api/v4
ALLOWED_PROJECTS=group/proj-a,group/proj-b   # optional Allowlist
```

---

## 7. Zweite Sicherungsschicht: GitLab-native Restriktionen

Die Sandbox (Warden) ist die **primäre** Durchsetzung. Versagt sie — Bug, Fehlkonfig,
Lücke im Filter —, müssen GitLab-seitige Restriktionen den **Worst Case** auffangen. Wir
gestalten die GitLab-Identität so, dass selbst ein „durchgereichtes" Write-Token nur
wenig Schaden anrichten kann.

### 7.1 Dedizierte Identität: Service Account (Premium)

- Ein **Group Service Account** (GitLab Premium) als Bot-Identität hinter den Tokens —
  **kein** menschlicher Account, kein `api`-Token eines Maintainers.
- Mitgliedschaft im Zielprojekt/-gruppe mit **Rolle = Developer** (oder maßgeschneiderte
  Rolle mit minimalen Rechten). Developer kann **nicht** auf protected Branches pushen
  und (bei korrekter Einstellung) **nicht** mergen.

### 7.2 Protected Branches → fängt R2, R4

- `main`/Default und Release-Branches (idealerweise per Wildcard `*` als Catch-all)
  **protecten**:
  - *Allowed to push and merge:* **No one** bzw. nur Maintainer/Owner.
  - Damit kann der Bot (Developer) auf keinen geschützten Branch pushen und keinen MR
    mergen — **auch wenn der Warden fällt** (Backstop für R2 und R4).

### 7.3 Push Rules (Premium) → fängt R2

- Unter **Push Rules** die **Branch-name**-Regex auf das Präfix setzen, z. B.
  `^claude/`. GitLab weist serverseitig jeden Push auf einen nicht passenden Branch ab —
  unabhängig vom Warden. Direkter nativer Backstop für R2.
- Zusätzlich „Committer restriction"/„Reject unsigned commits" je nach Bedarf.

### 7.4 Merge-Sperre → fängt R4

- Projekt-Einstellung *Merge requests → wer darf mergen* plus „Allowed to merge: No one
  (außer Maintainer)" auf protected Branches.
- Optional **Approval Rules**, die Approval durch jemand anderen als den Autor
  verlangen → der Bot kann nicht selbst genehmigen und mergen.
- Ergebnis: Selbst mit `api`-Token kann der Bot keinen Merge auslösen.

### 7.5 Token-Scopes → minimieren

- **Zwei** Tokens statt eines:
  - *Read-Token:* `read_api`, `read_repository` — für den Lesepfad.
  - *Write-Token:* `api` — nur für MR/Comment/CI; die Einschränkung „nur Claude-Branches"
    kommt aus Warden **und** Push Rules/Protected Branches, nicht aus dem Scope (GitLab
    hat keinen feineren „nur MR"-Scope).
- **Kurze TTL** (z. B. 7–30 Tage), Rotation, separate Tokens je Umgebung.

### 7.6 CI-Secrets → natürliche Sperre

- **Protected CI/CD-Variablen** werden nur auf protected Branches/Tags injiziert. Da der
  Bot ausschließlich auf `claude/*` (nicht protected) pushen kann, sehen seine Pipelines
  **keine** geschützten Secrets. Damit ist der Schaden eines getriggerten Jobs begrenzt.

### 7.7 Beobachtbarkeit

- **Audit Events** (Premium) auf Gruppen-/Projektebene aktivieren; Bot-Aktionen sind
  über den Service-Account klar zuordenbar und alarmierbar (z. B. unerwarteter
  Merge-Versuch).

### 7.8 Regel → Durchsetzung (zwei Schichten)

| Regel | Layer 1 — Warden (primär)                                   | Layer 2 — GitLab nativ (Backstop)                                  |
| ----- | ----------------------------------------------------------- | ------------------------------------------------------------------ |
| R1    | Read-Token, GET-Passthrough                                 | `read_api`/`read_repository`-Scope                                  |
| R2    | `pre-receive`-Hook: Präfix-Check                            | **Push Rules** Branch-Regex `^claude/` + Protected Branches        |
| R3    | API-Filter: Ownership + Endpoint-Allowlist                  | Developer-Rolle (kein Schreibrecht außerhalb eigener Branches)     |
| R4    | API-Filter: Merge-Endpunkte → 403                          | Protected Branches „merge: No one" + Approval-Regeln               |
| R5    | Quoten-/Rate-State (SQLite, Token-Bucket)                   | *(nativ nur grob über Plattform-Rate-Limits — Warden ist primär)* |
| R6    | Kein Token im Agenten + Netz-Isolation + Vertrauensgrenze   | Minimal-Scopes + Service-Account-Rolle begrenzen Worst-Case-Schaden |

R5 ist die einzige Regel **ohne** vollwertigen nativen Backstop — gitlab.com bietet nur
grobe Plattform-Rate-Limits. Hier trägt der Warden die Hauptlast; als zusätzliche
Absicherung kann ein externer Watchdog (Cron) periodisch offene Claude-MRs/Branches
zählen und bei Überschreitung Alarm schlagen oder automatisch schließen.

---

## 8. Restrisiken & offene Punkte

- **Warden ist Single Point of Trust.** Sein Code muss klein, auditierbar und gut
  getestet sein. Default-deny in beiden Pfaden (API & git).
- **Reconcile-Genauigkeit (R5):** lokaler Zähler vs. echte GitLab-Wahrheit muss
  periodisch abgeglichen werden (manuell geschlossene MRs, von außen gelöschte Branches).
- **git-Smart-HTTP-Proxy ist fummelig.** Falls der Aufwand zu hoch wird → Option D
  (Schreiben per Commit-API) als Fallback.
- **Push Rules / Protected-Branch-Semantik** auf gitlab.com vor Verlass im konkreten
  Setup verifizieren (UI-Pfade und genaue Wirkung können sich ändern).
- **Read-Token sieht alles, was es sehen darf.** „Lesen auf alles" (R1) heißt: Scope und
  Projektmitgliedschaft des Read-Tokens definieren die Lese-Reichweite — bewusst über
  `ALLOWED_PROJECTS`/Gruppenmitgliedschaft eingrenzen.

---

## 9. Umsetzungs-Roadmap

1. **Token-Leak schließen (sofort):** `GITLAB_API_TOKEN` aus dem `claude-dev-env`-Service
   in `docker-compose.yml` entfernen; MCP-Registrierung im Agenten so umbauen, dass kein
   echtes Token in der Agent-Env landet.
2. **Netz-Isolation:** `agent-net` als `internal: true`; Warden als einziger Egress.
3. **Warden v0 (Lesen):** Reverse-Proxy mit Read-Token, GET-Passthrough; Agent-MCP auf
   Warden umstellen. R1 verifizieren.
4. **Warden v1 (API-Schreiben):** Endpoint-Allowlist, Ownership-Checks, Merge-403,
   Quoten-/Rate-State. R3/R4/R5 verifizieren.
5. **Warden v2 (git-Schreiben):** bare Mirror + `pre-receive`-Hook + Forwarder. R2
   verifizieren.
6. **GitLab-Layer-2:** Service Account anlegen, Developer-Rolle, Protected Branches,
   Push Rules, Merge-Sperre, Audit Events. Mapping aus §7.8 abhaken.
7. **Red-Team-Test:** Bewusst versuchen, jede Regel aus dem Agenten heraus zu umgehen
   (Env-Dump, direkter gitlab.com-Connect, Force-Push, Merge-Call, Quoten-Flooding).
