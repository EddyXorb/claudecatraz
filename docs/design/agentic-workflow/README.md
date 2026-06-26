# Agentic Workflow — GitLab-Sandbox für Claude

Designdokument für ein „Claude-Gefängnis" in Docker, aus dem heraus Claude GitLab
benutzen kann wie über ein MCP — aber unter scharfen, nicht umgehbaren Restriktionen.

Status: **Entwurf / Planung.** Dieses Dokument legt die Regeln, das Bedrohungsmodell
und mehrere Architekturoptionen fest. Es ist noch keine Implementierung.

---

## Umsetzungspläne (Reihenfolge)

Dieses Dokument ist die **Spezifikation** (Regeln, Bedrohungsmodell, Architektur). Die
konkrete Umsetzung ist in nummerierte Pläne aufgeteilt. Das Zahlenpräfix gibt die
**Reihenfolge** an; **gleiches Präfix = parallel umsetzbar** (keine Abhängigkeit
untereinander):

| Stufe | Plan | Inhalt | Abhängt von |
| ----- | ---- | ------ | ----------- |
| **01** | [`01-bootstrap-hardening.md`](./01-bootstrap-hardening.md) | Token-Leak schließen (§4/R6), dediziertes Claude-Konto (§3.2) | — |
| **01** | [`01-gitlab-native.md`](./01-gitlab-native.md) | GitLab-native Schicht §7 (Service Account, Protected Branches, Push Rules, Tokens, Read-Scope) — Zero-Code | — |
| **02** | [`02-warden.md`](./02-warden.md) | Warden: API-Filter, git-G1-Proxy, State, Logging (§6) | 01 |
| **02** | [`02-forward-proxy.md`](./02-forward-proxy.md) | Research-/Build-Egress: Squid-Allowlist, Netz-Isolation (§6.6) | 01 |
| **03** | [`03-observability.md`](./03-observability.md) | Log-Viewer + optional Grafana/Loki (§6.8) | 02 |
| **03** | [`03-testing-redteam.md`](./03-testing-redteam.md) | Teststrategie, Red-Team-Suite, CI (§8) | 02 |

Begründung der Stufen: **01** ist kostenlos/Zero-Code und schließt sofort die akute
R6-Lücke — beide 01-Pläne sind voneinander unabhängig. **02** baut die zwei Egress-
Container (Warden, Forward-Proxy); sie sind untereinander unabhängig, brauchen aber die
01-Grundlage (Tokens/Service-Account bzw. Token-Entfernung). **03** ist additiv und setzt
den laufenden 02-Stack voraus. Die ausführliche, schritt-granulare Roadmap steht in §10;
die **konsolidierte, vollständige `docker-compose.yml` samt `config/`-Ordner** (das
Synthese-Artefakt aller Pläne) steht in **§11**.

---

## Begriffe (vorab)

Zwei Begriffe tauchen im ganzen Dokument auf; hier vorab erklärt:

- **Egress** — der ausgehende Netzwerkverkehr eines Containers, also „was darf der
  Container nach draußen erreichen?". *Egress sperren/isolieren* heißt: dem Container
  verbieten, beliebige Server im Internet zu kontaktieren. *Egress-Punkt* = die eine
  Stelle, über die ausgehender Verkehr noch erlaubt ist.
- **Warden** (engl. „Wächter/Türsteher") — unser **eigener Name** für die zentrale
  Vermittler-Komponente, die zwischen Claude und GitLab sitzt. Sie hält als Einzige die
  GitLab-Zugangsdaten und setzt alle Regeln durch: Sie prüft jede Anfrage und lässt nur
  Erlaubtes passieren. Kein Produktname, sondern eine in diesem Dokument definierte
  Komponente (Details in §6).

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

**Abgrenzung — Internet ≠ GitLab-Macht:** Die Regeln R1–R6 hängen **nicht** davon ab,
ob der Agent Internet hat. Sie werden durch Credential-Isolation im Warden erzwungen;
ohne GitLab-Token kann der Agent auch mit offenem Internet nichts gegen `gitlab.com`
tun. Der Internet-Egress des Agenten regelt eine **andere** Bedrohung —
**Datenexfiltration** und **Supply-Chain** — und ist ein separater Regler (siehe §6.6).
Research-Fähigkeit kann man also freigeben, ohne R1–R6 anzutasten.

### 3.1 Scope — was diese Architektur leistet und was nicht

Das adversarische Prämisse aus §3 (Agent kann Prozesse starten, Dateisystem lesen, etc.)
ist **weiter** als das, was der Warden allein abdeckt. Der Warden ist eine **Tür**, kein
Gebäudeschutz. Ehrlich abgegrenzt:

**Was die Architektur durchsetzt:** Die **GitLab-Wirkung** des Agenten bleibt innerhalb
R1–R6 — egal wie böswillig der Agent intern ist. Er kann keine fremden Branches
beschreiben, nichts mergen, keine Quoten sprengen.

**Was sie bewusst *nicht* leistet** (und wodurch es stattdessen aufgefangen wird):

- **Code-Poisoning des Arbeitsbaums.** Ein böswilliger Agent kann den Quellcode, an dem
  er arbeitet, manipulieren — dafür braucht er GitLab gar nicht. Aufgefangen wird das
  **nicht vom Warden**, sondern durch **R4 + menschliches MR-Review**: nichts erreicht
  `main` ohne einen von einem Menschen gereviewten Merge. Der Mensch im Merge-Gate ist
  die eigentliche Anti-Poisoning-Kontrolle; der Warden sorgt nur dafür, dass es beim
  Review-Gate bleibt (kein Self-Merge, kein Push auf geschützte Branches).
- **Lesen/Exfiltration (R1).** Lese-Reichweite ist eine Exfil-Fläche; das wird in §6.10
  analysiert und **begrenzt, nicht eliminiert**.
- **Andere Secrets im Agent-Container.** Per §3-Axiom kompromittiert — daher gehört
  **nichts** in den Agent-Container außer dem, was er zwingend braucht. Kein GitLab-Token,
  keine fremden Keys. Das eine, was zwingend drin liegt — die **Claude-Credentials** — ist
  damit die irreduzible Schwachstelle und wird in **§3.2** eigens analysiert.

Kurz: Die Architektur verspricht **„die GitLab-Aktionen des Agenten sind policy-konform"**,
nicht **„der Agent ist vertrauenswürdig"**. Wer Letzteres braucht, muss zusätzlich das
Code-Review-Gate (R4), die Lese-Scope-Begrenzung (§6.10) und den Schutz der
Claude-Credentials (§3.2) als gleichwertige Kontrollen behandeln — nicht als Fußnoten.

### 3.2 Die irreduzible Schwachstelle: Claude-Credentials im Agent-Container

Alle anderen Credentials lassen sich aus dem Agenten heraushalten (GitLab → Warden). Die
**Claude-Credentials nicht** — der Agent *ist* der Konsument: Claude Code braucht sie zum
Inferenzieren und für Remote Control. Sie liegen in `~/.claude/.credentials.json`
(Bind-Mount), enthalten bei Abo-Auth **Access- und Refresh-Token** (oder alternativ einen
`ANTHROPIC_API_KEY`), und der Agent hat darauf vollen Zugriff. Per §3-Axiom: **als
kompromittiert zu behandeln.**

**Schadenspotenzial eines böswilligen Agenten** (mit ehrlicher Unsicherheit über den
exakten OAuth-Scope-Umfang — die mit ⚠️ markierten Punkte vor Verlass verifizieren):

| Aktion | Einschätzung |
| ------ | ------------ |
| **Credential exfiltrieren** (inkl. Refresh-Token) | **Größter realer Schaden.** Off-box-Zugriff über die Session hinaus, bis revoziert. Einziger technischer Schutz: Egress-Lock (§6.6) — der bequemste Exfil-Pfad. |
| **Inferenz-Quota / Kosten verbrennen** | **Real.** Abo-Limits ausschöpfen; bei API-Key echtes Geld. Bounded durch Account-Wahl/Budget (s. u.). |
| **Alle Remote-Control-Sessions des Kontos sehen/steuern** | ⚠️ **Plausibel** — das Token *ist* das Konto, RC-Sessions hängen am Konto. Verifizieren. |
| **Konversations-Historie / Projekte lesen** | ⚠️ Scope-abhängig, unklar — verifizieren. |
| **Konto löschen / Passwort / Billing ändern** | ⚠️ **Sehr wahrscheinlich nein** (braucht Web-Session-Reauth, nicht den Inferenz-Scope) — aber nicht garantiert. |

**Warum man es nicht wie GitLab wegproxyen kann:** Bei der aktuellen **Remote-Control +
Abo-OAuth**-Bauweise authentifiziert sich Claude Code direkt nach außen — es gibt keine
Stelle, an der ein Warden-artiger Proxy die Auth injizieren könnte, ohne RC zu brechen.
Der einzige Fall, in dem ein **Auth-injizierender Modell-Proxy** (analog zum Warden, hält
die Credential, Agent kennt sie nicht) sauber ginge, ist eine **API-Key-, Nicht-RC-**
Konfiguration mit `ANTHROPIC_BASE_URL` auf den Proxy. ⚠️ RC/OAuth-Kompatibilität
verifizieren, bevor man darauf baut.

**Mitigation = Blast-Radius reduzieren** (nicht Isolation, die ist hier unmöglich),
stärkste zuerst:

1. **Dediziertes Wegwerf-Konto für die Sandbox** — *nicht* dein Primär-/Org-Konto. Dann
   bedeutet „kompromittiert": leere Historie, nur Sandbox-RC-Sessions, separates
   Quota-Limit, Löschung egal. Macht die meisten ⚠️-Zeilen oben irrelevant. **Wichtigste
   Einzelmaßnahme.**
2. **Budget-gedeckelter API-Key statt Abo-Token**, wo möglich — harte Kostenobergrenze,
   trivial revozierbar, ermöglicht zudem den Auth-Proxy (s. o.). Trade-off: Pay-per-use
   statt Abo, ggf. kein Remote Control.
3. **Egress-Lock (§6.6)** als einziger technischer Exfil-Schutz — Allowlist eng, sonst
   kann der Agent die `.credentials.json` einfach raus-POSTen.
4. **Revozierbarkeit & Monitoring** — Token schnell rotierbar halten; Konto-Nutzung auf
   Anomalien (Inferenz-Spikes, fremde RC-Sessions) beobachten.
5. **Mount-Hygiene** — nur die Credential-Datei mounten, kein weiteres Konto-Material;
   bind-mount möglichst eng.

#### Sonderfall: RC-Session-Hijack & laterale Bewegung (Worst Case)

Wenn der Agent über das Token nicht nur RC-Sessions **sehen**, sondern sich auch mit
ihnen **verbinden und sie steuern** kann, wird aus „GitLab-Missbrauch" ein **Ausbruch aus
der Sandbox**: Er könnte Claude auf deinem Laptop oder in anderen Containern fernsteuern
und so an Systeme gelangen, die mit der Sandbox nichts zu tun haben. Das ist die
gefährlichste denkbare Eskalation hier.

⚠️ Ob das Code-OAuth-Token fremde Sessions tatsächlich enumerieren **und** ansteuern kann,
ist **nicht autoritativ geklärt und vor Inbetriebnahme zu verifizieren**. Die Design-
Haltung ist trotzdem: **annehmen, dass es geht, und die Fläche strukturell beseitigen** —
nicht auf „wahrscheinlich nicht" verlassen.

Prävention, gestaffelt (strukturell zuerst):

- **Konto-Isolation beseitigt das Hijack-Ziel.** Mit einem **dedizierten Konto, das für
  *nichts* anderes verwendet wird** (nie auf Laptop/Primär-Claude eingeloggt, steuert
  keine anderen Agenten), sind die *einzigen* RC-Sessions dieses Kontos die der Sandbox
  selbst. „Self-Hijack" ist bedeutungslos, laterale Bewegung gibt es nicht. Konten sind
  die Grenze — das Sandbox-Konto sieht die Sessions deines Primärkontos nicht.
- **Ein Konto pro Sandbox-Instanz**, wenn mehrere Sandboxen unterschiedliche Trust-/
  Zugriffsniveaus haben → eine kompromittierte Sandbox kann keine andere übernehmen.
- **Remote Control ganz weglassen** ist der sauberste Hebel: RC ist *das* Feature, das
  fernsteuerbare Sessions erst erzeugt. Ein Headless-/API-Key-Betrieb, von deiner eigenen
  Orchestrierung getrieben, hat **keine RC-Sessions** zum Enumerieren/Kapern — die ganze
  Klasse entfällt. Trade-off: kein Browser-Driving über claude.ai.
- **Revozieren & Alarmieren** auf unerwartete/fremde RC-Sessions als Erkennungsnetz.

**Konsequenz fürs Design:** Die Sandbox läuft **grundsätzlich mit einem dedizierten,
ausschließlich dafür genutzten, budget-/scope-begrenzten Claude-Konto**. „Mein
persönliches Konto reinmounten" ist unter der adversarischen Prämisse keine vertretbare
Default-Konfiguration — und wo Remote Control nicht zwingend gebraucht wird, ist der
RC-lose Betrieb die sicherere Wahl. Die exakten RC-Fähigkeiten des Tokens sind ein
**offener Verifikationspunkt** (§9).

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
- **Git-Schreibpfad:** transparenter Smart-HTTP-Filterproxy (§6.2, „G1") — inspiziert die
  Ref-Kommandos im `receive-pack`-Strom (Präfix, Branch-Zahl, Rate) und streamt erlaubte
  Pushes an `gitlab.com` weiter. **Ohne** bare-Repo/Mirror, der Agent benutzt echtes `git`.
- **Lesepfad:** API-GETs und git-fetch werden mit einem **read-only**-Token
  durchgereicht.

Der Agent hält **kein** GitLab-Token. Das MCP des Agenten zeigt auf den Warden, nicht
auf gitlab.com; selbst wenn der Agent das MCP umgeht, kommt er per Netz nur bis zum
Warden.

| R1 | R2 | R3 | R4 | R5 | R6 |
| -- | -- | -- | -- | -- | -- |
| ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

**Empfohlen — abgestuft, aber mit echtem git von Anfang an.** Die GitLab-native Schicht
(§7) kommt **zuerst** (R1/R2/R4 nativ, R3 teilweise, kostenlos); der Eigenbau beschränkt
sich auf den gemessenen Rest — **R5 (Quoten/Rate) und R3-Ownership** im API-Filter. Der
Schreib-**Pfad** ist hier aber kein freier Parameter mehr: Der Workspace ist
bind-gemountet und wird **vom Host aus** bearbeitet/inspiziert (VSCode), Agent und Mensch
teilen sich **denselben Working-Clone**. Damit scheidet die Commit-API (Option D) als
Standard-Schreibpfad aus — sie baut serverseitig Commits mit **abweichender SHA** und
zwingt diesen geteilten Clone in dauerhafte Divergenz (§6 / §9.1). Deshalb gehört der
**transparente git-Smart-HTTP-Proxy (§6.2, G1) in die Start-Konfiguration** — er ist
dank Stream-Inspektion ohne Mirror/Hook/Forwarder schlank genug dafür. Option D bleibt
nur Sonderfall-Fallback (ephemerer Agenten-Clone ohne Host-Working-Copy). Details in §6,
Abwägung in §9.1, Reihenfolge in §10.

### Option D — API-only-Schreibmodell (kein git-Push, Commit-API)

Wie C, aber Schreiben erfolgt **ausschließlich** über die GitLab-API
(`POST .../repository/commits`) statt git-Protokoll. Der Warden braucht dann **keinen**
git-Smart-HTTP-Pfad — nur den API-Filter, den er für R3/R5 ohnehin hat. Eine einzige,
voll in HTTP-Logs sichtbare Protokoll-Oberfläche → deutlich kleiner abzusichern und zu
warten.

**Aber kein freier Tausch** — Option D erbt eigene Korrektheits-Risiken (Details in
§9.1):

- **Lokal ↔ remote divergiert garantiert:** die Commit-API baut serverseitig einen Commit
  mit **anderer SHA** als die lokale Kopie → der Agent muss nach jedem Write `git fetch`en
  und das Remote als Wahrheit behandeln. Das ist eine echte Disziplin, kein Komfort-Detail.
- **Working-Tree → `actions[]`-Übersetzer nötig:** „was hat sich geändert" muss in die
  API-Action-Liste übersetzt werden (`create`/`update`/`delete`/`move`/`chmod`, `text`
  vs. `base64`). Genau hier sehen Bugs wie Korruption aus (verlorene Renames, mangled
  Binaries) — obwohl das git-Objektlager intakt bleibt.
- **`force: true` existiert auch an der API** → „API-only" erfüllt R2 **nicht**
  automatisch; der Warden/Push Rules müssen Force weiterhin abweisen.
- **Merges/Rebases/Cherry-Picks** bilden sich nicht sauber ab; für einen Vorwärts-nur-
  Agenten auf `claude/*` aber verschmerzbar.

| R1 | R2 | R3 | R4 | R5 | R6 |
| -- | -- | -- | -- | -- | -- |
| ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

**Nur für ephemere Agenten-Clones — in diesem Setup ausgeschlossen.** Für einen engen
Sandbox-Workflow mit *wegwerfbarem* Agenten-Clone (Agent klont, committet vorwärts-nur,
öffnet MR, Clone wird verworfen) wäre der Tausch tragfähig. **Hier aber nicht:** Der
Workspace ist bind-gemountet und wird vom Host (VSCode) mitbearbeitet — Agent und Mensch
teilen einen langlebigen Clone. Die garantierte SHA-Divergenz der Commit-API (§9.1) würde
genau diesen Clone dauerhaft gegen den Server auseinanderlaufen lassen. Deshalb ist der
Standard-Schreibpfad **git-Push über G1 (§6.2)**, nicht Option D. D bleibt dokumentiert
als Fallback, falls man den git-Proxy partout vermeiden will *und* den Host-Clone aufgibt.
Die Entscheidung G1 vs. D ist **kein Komfort-, sondern ein Korrektheits-Trade-off** und
wird in §9.1 vollständig abgewogen.

---

## 6. Empfohlene Architektur im Detail (Option C — Warden)

> **Start-Konfiguration vs. Vollausbau — schlank, aber mit echtem git.**
> Gebaut wird die **reduzierte Start-Variante**. „Schlank" heißt hier *nicht* „ohne
> git-Proxy": Der Smart-HTTP-Proxy (G1) ist dank Stream-Inspektion ohnehin leicht und für
> das Host-Workspace-Setup (siehe Designtreiber unten) **notwendig**. Was wegfällt, ist
> der MCP-Sidecar und jedes Mirror/Hook/Forwarder-Gerüst.
>
> | Baustein | Start (MVP) | Vollausbau (nur bei Bedarf) |
> | -------- | ----------- | --------------------------- |
> | GitLab-native Schicht (§7) | **ja, zuerst** (R1/R2/R4 nativ) | — |
> | Warden als **API-Filter** (§6.3), Python | **ja** (R3-Ownership + R5) | — |
> | Schreibpfad | **git-Push über Smart-HTTP-Proxy (G1, §6.2)** | — |
> | Commit-API / Option D (§9.1) | **nein** (SHA-Divergenz bricht den Host-Clone) | nur als Fallback, falls man den git-Proxy vermeidet *und* den Host-Clone aufgibt |
> | Forward-Proxy für Research (§6.6) | **ja** | — |
> | **MCP-Sidecar** | **nein** — `git` + REST gegen den Warden | winziges Erst-Partei-MCP, **nur** falls der Agent mit rohem REST messbar stolpert (§6.12) |
>
> **Designtreiber — Host-seitige Arbeit am Workspace.** `workspace/` ist bind-gemountet
> und wird **vom Host aus** inspiziert und bearbeitet (VSCode, *außerhalb* des Containers).
> Agent und Mensch teilen sich denselben langlebigen Working-Clone und dasselbe `.git`.
> Damit muss der Schreibpfad **SHA-erhaltend** sein: `git push` lädt exakt den lokalen
> Commit hoch → Host-Clone, Agent und Server bleiben kohärent. Die Commit-API baut
> serverseitig fremde SHAs und würde den geteilten Clone dauerhaft divergieren lassen
> (§9.1) — daher G1 statt Option D.
>
> **Warum es trotzdem schlank bleibt:** Die GitLab-REST-API steckt im Trainingswissen
> jedes großen LLM — Claude bedient sie über `git` + `curl`/`httpx` direkt, ohne
> MCP-Tool-Schicht (§6.12). G1 fügt nur vier git-Routen + einen pkt-line-Ref-Parser auf
> dem vorhandenen Warden hinzu, **kein** zweites Repo. So bleibt der Eigenbau ein schlanker
> Python-Proxy statt ein „Mini-GitLab". Reihenfolge in §10.

### 6.1 Netz- und Container-Topologie

Start-Variante (kein MCP; echtes git per transparentem Smart-HTTP-Proxy, kein Mirror).
Der Host bearbeitet denselben bind-gemounteten Workspace mit:

```
   Host (VSCode)                          ┌───────────────────────────────────┐
   editiert workspace/  ◀── bind-mount ──▶│ docker network: agent-net         │
   (außerhalb Container)                  │  (internal: true → KEIN Egress)   │
                                          │                                   │
   ┌────────────────────┐  git push/fetch ┌───────────────────────┐          │
   │  claude-dev-env     │───── + REST ───▶│      gitlab-warden     │         │
   │  (Agent)            │  (echtes git,   │  - API-Filter (Python) │         │
   │  KEIN GitLab-Token  │   curl/httpx)   │  - git Smart-HTTP-Proxy│         │
   │  git+REST → warden  │                 │    (pkt-line-Inspekt.) │         │
   │  http_proxy → fwd   │                 │  - Quoten-/Rate-State  │         │
   └─────────┬──────────┘                  │  - hält ALLE Tokens    │         │
             │ HTTP(S) CONNECT             └───────────┬───────────┘          │
             ▼                              ┌───────────┴──────────┐          │
   ┌────────────────────┐                   │  gitlab.com / api/v4 │          │
   │  forward-proxy     │                   └──────────────────────┘          │
   │  (Squid, Allowlist)│─ egress-net ─▶ npmjs/crates/pypi/conan, docs.*, …   │
   └────────────────────┘                                                     │
                                          └───────────────────────────────────┘
       Agent erreicht das Internet NUR über Warden (GitLab) bzw.
       forward-proxy (Research/Build, allowlistgefiltert).
       Schreiben per echtem git push über G1 (§6.2) → SHA-erhaltend, der
       Host-Clone bleibt kohärent. Kein MCP-Sidecar (§6.12), kein Mirror.
```

Schlüsselmaßnahmen:

- **`agent-net` ist `internal: true`** → der Agent-Container hat keinerlei
  Internet-Route. Sein einziger Nachbar ist der Warden.
- Der **Warden** hängt zusätzlich an einem zweiten Netz mit Internet-Egress. Optional
  über die Firewall auf `gitlab.com` allowlisten.
- Im Agent-Container: **kein** `GITLAB_API_TOKEN`, **kein** `GITLAB_GIT_TOKEN`, **kein**
  `.netrc`. Die REST-Basis-URL zeigt auf den Warden; git nutzt eine kanonische Remote-URL
  + globalen `insteadOf`-Rewrite auf den Warden (W3.1). Es gibt kein Agent-seitiges
  Credential — der Warden verlangt keine Auth (W9.3).
- Agent läuft als non-root (wie heute), aber das ist hier zweitrangig — die Grenze ist
  das Netz, nicht der User.
- **Host-seitiges Arbeiten:** `workspace/` ist bind-gemountet; VSCode auf dem Host
  editiert/inspiziert denselben Clone. Schreiben läuft SHA-erhaltend über `git push` (G1),
  damit Host-Clone und Server nicht divergieren (Designtreiber, §6).
- **Vollausbau** ergänzt optional nur noch ein winziges Erst-Partei-MCP (§6.12) — bei
  gemessenem Bedarf. Der git-Proxy ist bereits Teil der Start-Variante.

### 6.2 Git-Schreibpfad (echtes `git`, ohne doppelten Repo-Bestand)

Der Agent braucht **echtes `git push`/`fetch`/`clone`** — nicht aus Komfort, sondern weil
der Host denselben Clone bearbeitet und der Schreibpfad SHA-erhaltend sein muss (§6,
Designtreiber). Für echtes git gibt es einen naheliegenden, aber teuren Weg — und einen
schlanken. Der teure zuerst, damit klar ist, was wir *nicht* tun:

> **Warum die naive Variante doppelt speichert.** Ein `pre-receive`-Hook läuft nur,
> wenn ein echtes git-Repo den Push *entgegennimmt*. Um den Hook auszuführen, müsste
> der Warden je Projekt ein **bare Mirror-Repo** halten und den Push dort
> terminieren — diese Materialisierung **ist** die zweite Kopie (neben dem
> Working-Clone des Agenten), plus Forwarder, der die validierten Refs erneut
> nach gitlab.com pusht. Das ist der „alle Repos zweimal"-Schmerz. **Wir vermeiden
> ihn.**

Schlüsseleinsicht: **Man muss einen Push nicht entgegennehmen, um ihn zu prüfen.**
Das Smart-HTTP-Protokoll legt die Ref-Updates im Klartext offen, *bevor* die
Binärdaten kommen. Der Warden inspiziert den Strom, statt ihn zu terminieren — und
speichert **nichts**.

**Empfohlen (G1) — transparenter Smart-HTTP-Filterproxy, null Repos.**
Der Warden bedient auf demselben Reverse-Proxy, den er für die REST-API ohnehin
betreibt, vier zusätzliche git-Routen:

```
GET  /<repo>/info/refs?service=git-upload-pack    → fetch-Advertise   (lesen, R1) → durchreichen
POST /<repo>/git-upload-pack                       → fetch            (lesen, R1) → durchreichen
GET  /<repo>/info/refs?service=git-receive-pack    → push-Advertise               → durchreichen
POST /<repo>/git-receive-pack                      → push                          → prüfen!
```

1. Agents Repo-Remote bleibt **kanonisch** (`https://gitlab.com/<project>.git`) — identisch
   zum Normal-Clone, damit der Host direkt im Workspace arbeiten kann. Die Umleitung auf den
   Warden steht **nur** in der globalen Container-git-Config:
   `git config --global url."http://gitlab-warden:8080/git/".insteadOf "https://gitlab.com/"`
   (Details + Begründung „Ergonomie ≠ Sicherheitsgrenze": [`02-warden.md` W3.1](./02-warden.md)).
2. **Lesen** (`upload-pack`, `info/refs`): unverändert an gitlab.com streamen, Read-Token
   injizieren. Nichts wird gespeichert (R1).
3. **Push** (`git-receive-pack`): Der Warden liest die **Kommando-Sektion** am Anfang des
   Request-Bodys. Sie besteht aus pkt-lines im Klartext, *vor* den PACK-Binärdaten:

   ```
   <4-hex-len><old-oid> SP <new-oid> SP <refname> NUL <capabilities> LF
   …weitere Ref-Kommandos…
   0000                       ← flush-pkt, danach folgt PACK……(binär)
   ```

   Pro Ref-Kommando prüft er:
   - `refname` beginnt mit `BRANCH_PREFIX` → sonst **reject** (R2).
   - `new-oid` = lauter Nullen (Branch-Löschung) → reject.
   - Anzahl existierender Claude-Branches < `MAX_OPEN_BRANCHES` (R5).
   - Rate-Limit `MAX_WRITES_PER_HOUR` nicht überschritten (R5).
4. Bei Annahme **streamt** der Warden den unveränderten Body (inkl. PACK) mit dem
   Write-Token an gitlab.com. Bei Ablehnung ein git-Fehler über das sideband. Der Agent
   sieht das Token nie und hält **nur seinen einen Working-Clone**.

Warum so: Der Agent benutzt echtes git, der Warden hält **kein bare-Repo, keinen Hook,
keinen Forwarder** — nur einen ~100-Zeilen-pkt-line-Parser auf einer ohnehin
vorhandenen Komponente. Das `receive-pack`-Wire-Format ist seit Jahren stabil. Damit
fällt der Hauptgrund weg, der ursprünglich für die Commit-API sprach (die
Mirror-Fummeligkeit) — G1 hat keinen Mirror.

> **Grenze von G1 — Force-Push.** Ob ein Push ein non-fast-forward (Force) ist, lässt
> sich aus dem Request allein nicht entscheiden (man bräuchte den Objektgraphen). Das
> delegiert man an **GitLab Protected Branches / Push Rules** (Layer 2, §7). Für R2
> zählt ohnehin nur der *Branchname* — und der steht vollständig im Kommando, also
> architektonisch im Trust-Bereich geprüft. Wer Force auch Warden-seitig hart blocken
> will, braucht G3.

**Schlanker (G2) — nur Credential-Inject, Ref-Policy GitLab-nativ.** Der Warden parst
gar kein git-Protokoll: Token injizieren, Pushes zählen (R5), loggen. Die Ref-Regeln
(`^claude/`, kein Force, kein Delete) macht **GitLab Push Rules** serverseitig — ebenfalls
architektonisch (R6-konform), aber R2 hängt dann an *einer* Schicht statt an Warden **+**
GitLab. Maximal billig, etwas weniger Tiefenverteidigung.

**Nur falls Objektgraph-Logik nötig (G3) — materialisieren, aber geteilt.** Will man
echte Ancestry-/Force-Checks gegen die Objekte, *ein* bare-Mirror mit git-Alternates
(`--reference`) als gemeinsamem Objektspeicher statt Vollkopie pro Klon. Lindert die
Doppelung, beseitigt sie nicht, und behält die Hook-Komplexität. Nur wenn man Logik
braucht, die wirklich den Graphen sieht.

**Verhältnis zur Commit-API (§9.1):** Commit-API ist *protokollfrei* (keine git-Inspektion,
eine HTTP-Oberfläche), erzwingt aber local↔remote-SHA-Divergenz und einen
Working-Tree→`actions[]`-Übersetzer. In **diesem** Setup ist die Divergenz disqualifizierend:
Der bind-gemountete Workspace wird vom Host (VSCode) mitbearbeitet; fremde Server-SHAs
würden den geteilten Clone dauerhaft auseinanderlaufen lassen. Deshalb ist **G1 der
Start-Schreibpfad**, nicht die Commit-API. D bleibt nur Fallback für den Sonderfall eines
ephemeren Agenten-Clones ohne Host-Working-Copy.

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

*Falls* ein MCP genutzt wird (es ist optional — Abwägung in §6.12), zeigt seine
`GITLAB_API_URL` auf den **Warden** statt auf gitlab.com und es hält **kein** echtes Token
(nur eines für den Warden) — bequemer Tool-Layer, aber keine Sicherheitsgrenze.

### 6.4 Zustandshaltung für Quoten (R5)

Der Warden führt eine kleine Datenbank (z. B. SQLite im Warden-Volume):

- Tabelle der vom Agenten erzeugten Branches/MRs (zum Zählen, ohne gitlab.com zu
  fluten — periodischer Reconcile gegen die API).
- gleitendes Fenster der Schreibaktionen pro Stunde (Token-Bucket).

Die Wahrheit über „offene MRs/Branches" lässt sich zusätzlich live aus der API
verifizieren (Reconcile), damit manuell geschlossene MRs das Kontingent freigeben.

**Wichtig — das ist ein synchroner Hard-Limit, kein Nachlauf-Alarm:** Die Prüfung läuft
**vor** jeder Schreibaktion und **blockt** die (N+1)-te inline. Der „Watchdog-Cron" in
§7.8 ist nur der *zusätzliche* Backstop, nicht die primäre R5-Durchsetzung. Damit dieser
Hard-Limit nicht durch einen provozierten Warden-Crash zurückgesetzt werden kann, gelten
die Durabilitäts- und Fail-safe-Regeln aus **§6.11** (WAL+fsync, „leer ⇒ gesperrt bis
Reconcile", Reconcile beim Start).

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

### 6.6 Research-Egress (Allowlist-Forward-Proxy)

Damit der Agent zur Umsetzung von Tasks **recherchieren und bauen** kann (Doku lesen,
Pakete ziehen), bekommt er einen zweiten, vom Warden **getrennten** Egress-Punkt: einen
Forward-Proxy mit kuratierter Domain-Allowlist. Dies betrifft **nur** Exfiltrations-/
Supply-Chain-Risiko, nicht R1–R6 (siehe §3, „Internet ≠ GitLab-Macht").

**Aufbau:**

- `agent-net` bleibt `internal: true` — der Agent hat **keine** direkte Internet-Route.
- Der **forward-proxy** (z. B. Squid) hängt zusätzlich am Internet-Egress und filtert
  per **Domain-Allowlist** (SNI/Host-basiert, kein TLS-MITM nötig). Default-deny.
- Im Agent-Container zeigen `http_proxy`/`https_proxy`/`no_proxy` auf den Forward-Proxy;
  der Warden bleibt der direkte Pfad für GitLab (über `no_proxy` ausgenommen).
- Der Forward-Proxy hält **keine** Credentials — er filtert nur Ziele.

**Allowlist-Kategorien (Start-Set, projektabhängig tunen):**

| Zweck            | Beispiele                                                            |
| ---------------- | ------------------------------------------------------------------- |
| Paket-Registries | `registry.npmjs.org`, `crates.io`, `static.crates.io`, `pypi.org`, `files.pythonhosted.org`, `center.conan.io` |
| Toolchain        | `apt.llvm.org`, `sh.rustup.rs`, `static.rust-lang.org`, `deb.nodesource.com` |
| Doku & Q&A       | `docs.gitlab.com`, `doc.rust-lang.org`, `docs.python.org`, `stackoverflow.com` |
| Code (read)      | `github.com`, `raw.githubusercontent.com`, `objects.githubusercontent.com` |

**Zusätzlich, unabhängig vom Proxy:** Claude Codes eingebaute **WebSearch** läuft
serverseitig über Anthropic, nicht über den Agenten-Egress → kann immer aktiv sein, null
Exfil-Risiko. Build-Egress (cargo/npm/pip/conan) ist Teil derselben Allowlist.

**Restrisiko:** Allowlistete Hosts mit Schreib-/Echo-Eigenschaften (z. B. GitHub-Gists,
Such-Endpunkte) bleiben theoretische Exfil-Kanäle. Liste eng halten, bei Bedarf
GitHub auf reine Raw-/API-Read-Pfade einschränken, Proxy-Logs auditieren.

**Konfiguration (Env des forward-proxy bzw. Agenten):**

```
# forward-proxy
EGRESS_ALLOWLIST=registry.npmjs.org,crates.io,pypi.org,center.conan.io,docs.gitlab.com,github.com,...

# Agent
http_proxy=http://forward-proxy:3128
https_proxy=http://forward-proxy:3128
no_proxy=gitlab-warden
```

### 6.7 Technische Gestaltung des Warden (Sprache/Framework)

Der Warden ist die **einzige Vertrauensgrenze** (§3). Auswahlkriterien daher in dieser
Reihenfolge: **Auditierbarkeit** (wenig Code, wenige Abhängigkeiten → kleine
Angriffsfläche), **HTTP-Reverse-Proxy mit Streaming** (git-Packfiles und API-Bodies
durchreichen, ohne sie ganz in den Speicher zu laden), **einfache git-Integration**
(Smart-HTTP-Streaming + pkt-line-Inspektion, §6.2) und **wartbare Policy-Logik**.

Der git-Pfad braucht in der empfohlenen Variante (§6.2, G1) **kein** eigenes git-Backend:
Der Warden streamt `upload-pack`/`receive-pack` transparent durch und parst nur die
Ref-Kommando-Sektion — also dieselbe Sprache/Runtime wie der API-Filter, kein
`git http-backend`, kein `pre-receive`-Hook. Die Sprachwahl betrifft damit den **gesamten**
Warden einheitlich: API-Filter, git-Streaming, State, Logging.

| Stack | Pro | Contra | Eignung |
| ----- | --- | ------ | ------- |
| **Python** (FastAPI/Starlette + `httpx` + `sqlite3`) | Sehr lesbar, schnell geschrieben; Team kennt Python (vgl. `entrypoint.py`); reichhaltige Test-Tools | Größeres Image + Runtime; async-Streaming großer Packfiles braucht Sorgfalt; mehr Dependencies (Supply-Chain) | **Gewählt** (siehe unten) |
| **Go** (`net/http`, `httputil.ReverseProxy`, `modernc.org/sqlite`) | Ein statisches Binary, winziges Container-Image, keine Runtime-Deps; exzellentes Streaming & Nebenläufigkeit; stdlib-Reverse-Proxy; ideal für eine kleine Security-Boundary | Etwas mehr Boilerplate; Team evtl. weniger vertraut | Stärkste Alternative |
| **Node/TypeScript** | Nähe zum bestehenden MCP-Ökosystem; viele Proxy-Libs | Schwerere Runtime, npm-Supply-Chain auf der Sicherheitsgrenze unschön | Nachrangig |
| **Envoy + ext_authz + Mini-Auth-Service** | Kampferprobte Proxy-Datenebene; Policy als separater, winziger Dienst (beliebige Sprache); robustes Streaming/Rate-Limit out of the box | Mehr bewegliche Teile; git-Push-Inspektion trotzdem separat nötig; steilere Lernkurve | Variante, wenn maximal gehärtete Datenebene gewünscht |

**Entscheidung: Python.** Der Warden-Kern (API-Filter, git-Smart-HTTP-Proxy, State, Logging) wird in
**Python** umgesetzt — wegen Team-Vertrautheit (vgl. `entrypoint.py`), Lesbarkeit und
reichhaltigem Test-Ökosystem (§8). Die Last (ein Agent, geringe Request-Rate) ist
unkritisch, daher wiegen Go-Vorteile wie statisches Binary/Streaming-Durchsatz hier nicht
schwer. Empfohlener Stack: **FastAPI/Starlette + `httpx` + `sqlite3`** (Standardbibliothek
für den State); git-Pfad (falls überhaupt nötig, vgl. §5/§10) als transparenter
Smart-HTTP-Streamingproxy mit pkt-line-Ref-Inspektion (§6.2, G1) — auf derselben
Starlette-App, ohne separates git-Backend. Wichtig bei Python: `receive-pack`-Bodies
**streamen**, nicht puffern (`StreamingResponse`/`httpx.stream`), damit große Packfiles
nicht in den Speicher laufen.

Damit Pythons schwächere Punkte (größeres Image, Runtime, mehr Dependencies auf der
Sicherheitsgrenze) nicht durchschlagen, gezielt gegensteuern:

- **Dependency-Disziplin:** so wenige Pakete wie möglich, gepinnt (Lockfile/Hashes), in
  CI per `pip-audit`/Supply-Chain-Scan geprüft. Die Vertrauensgrenze soll auch in Python
  klein und lesbar bleiben.
- **Schlankes, gepinntes Base-Image** (`python:3.x-slim` oder distroless-python),
  non-root, read-only Root-FS wo möglich.
- **Streaming bewusst:** API-Bodies und (falls genutzt) git-Packfiles als Streams
  durchreichen (`httpx`-Streaming, `StreamingResponse`), nicht komplett in den Speicher —
  in Python die Stelle, die Sorgfalt braucht.

Unabhängig von der Sprache:

- **Policy als reine Funktion** `decide(request, state) → Allow|Deny(reason, rule)`,
  getrennt von HTTP-/git-Transport → direkt unit-testbar (§8).
- **Default-deny** in beiden Pfaden; jede Allow-Regel explizit.
- **Minimales, gepinntes Base-Image**, non-root.

### 6.8 Transparenz & Logging

Der Nutzer muss jederzeit nachvollziehen können, **was durch den Warden ging**. Der
Warden ist die einzige Stelle, durch die GitLab-Verkehr fließt — also der natürliche,
vollständige Audit-Punkt.

**Was geloggt wird** — jede Entscheidung in beiden Pfaden, ein Eintrag pro Vorgang:

- Zeitstempel (UTC, ms), Kanal (`api` | `git`), Korrelations-ID.
- API: Methode + Pfad (query-sanitisiert), Ziel-Projekt, Entscheidung
  (`allow`/`deny`) + **welche Regel** (R1–R5), Upstream-Status, Latenz, Bytes.
- git: Operation (`push`/`fetch`), je Ref `old→new`, Branchname, Entscheidung + Regel.
- Quoten-Stand zum Zeitpunkt (offene MRs, Branches, Writes in der Stunde).
- **Redaction:** `Authorization`-Header und Token-Werte werden **nie** geloggt.

**Format & Rotation:**

- **Quelle der Wahrheit: JSONL** (`warden-audit.jsonl`) — ein JSON-Objekt pro Zeile,
  maschinenlesbar, leicht zu tailen/ingestieren.
- **Menschlich lesbar:** zusätzlich ein gerendertes `.txt` (eine Zeile pro Ereignis,
  ausgerichtet) — entweder parallel geschrieben oder on-demand aus dem JSONL erzeugt.
- **Rotation:** größen-/zeitbasiert (z. B. 50 MB / täglich), N Generationen, gzip der
  alten. Rotation per **rename+reopen** (nicht `copytruncate`), um die u. g. Lese-Races
  zu vermeiden.

**Keine Race-Conditions** (explizite Anforderung):

- **Genau ein Schreiber:** alle Log-Writes laufen durch *einen* Logger im Warden,
  serialisiert über einen Mutex bzw. einen Channel (Go) — nebenläufige Requests können
  Zeilen nicht verschränken.
- **Atomare Zeilen:** jeder Eintrag wird als **eine** vollständige Zeile in einem
  Write geschrieben (append-only, `O_APPEND`). Leser sehen so immer ganze Zeilen, nie
  Fragmente — gleichzeitiges Lesen während des Schreibens ist sicher.
- **Rotation ohne Race:** beim Rotieren wird die Datei umbenannt und neu geöffnet; ein
  tailender Leser folgt sauber dem Inode statt auf eine truncate-Lücke zu treffen.
- **Leser sind strikt read-only** und beeinflussen die Policy nie (eigener Pfad/Port).
- **Logging blockiert die Policy nicht:** schlägt das Schreiben fehl, wird die
  Entscheidung trotzdem durchgesetzt und der Fehler auf stderr gemeldet (fail-safe).

**Im Browser lesen — zwei Ausbaustufen:**

1. **Leichtgewichtig:** ein **read-only** Log-Viewer (statische Seite, die das JSONL mit
   Filter nach Kanal/Regel/Entscheidung rendert), auf separatem Port, nur im internen
   Netz erreichbar. Minimale Infra, kein Schreibzugriff.
2. **Grafana + Loki:** Warden schreibt JSONL → Promtail/Alloy tailt → **Loki** →
   **Grafana**-Dashboards (Requests/h, Deny-Quote je Regel, Quoten-Auslastung, Alarme bei
   Merge-Versuch). Mehr Container, dafür Queries, Zeitreihen und Alerting. Empfohlen,
   sobald Dashboards/Alarme gewünscht sind; das JSONL bleibt die Quelle der Wahrheit, der
   Stack ist additiv.

Beide Stufen lesen nur — der Warden bleibt alleiniger Schreiber, daher gilt die
Race-Freiheit unverändert.

### 6.9 Wartbarkeit & API-Stabilität

**Sorge:** Die GitLab-API ändert sich, der Warden müsste ständig nachgezogen werden.
**Antwort:** Der Warden bildet die API nicht nach — er reicht sie durch. Die zu wartende
Kopplung ist dadurch klein und liegt am stabilsten Teil der API.

**Wie stabil ist die GitLab-API?** Die **REST-API v4** ist seit 2017 stabil; GitLab
sichert Rückwärtskompatibilität innerhalb v4 zu. Änderungen sind nahezu immer **additiv**
(neue Felder/Endpoints); echte Breaking Changes durchlaufen einen **Deprecation-Prozess**
mit langer Vorlaufzeit und sind an Major-Releases gebunden. Die stark churnende Fläche
ist die **GraphQL-API** — die der Warden **nicht** braucht. Pfad-Templates wie
`POST /projects/:id/merge_requests` oder `PUT .../merge_requests/:iid/merge` gehören zu
den langlebigsten Teilen.

**Warum die Kopplung klein ist:**

- **Reads = transparenter Pass-Through.** Der Warden parst keine Antwort-Schemata. Neue
  Lese-Endpoints/-Felder wirken sofort, **ohne Codeänderung** (R1). Das ist der weitaus
  größte und churn-anfälligste Teil der API — und er kostet null Wartung.
- **Writes = Muster-Allowlist + Feld-Extraktion.** Nur die wenigen Schreib-Endpoints aus
  R2–R4 werden erkannt; daraus werden **nur** die Entscheidungsfelder gezogen
  (`source_branch`, Autor, Merge-Absicht), nicht das ganze Payload-Schema validiert.
- **Default-deny macht Veralten sicher, nicht gefährlich.** Ein unbekannter neuer
  Schreib-Endpoint wird **geblockt** und geloggt — nie still durchgelassen. Eine
  API-Änderung kann damit schlimmstenfalls ein neues Feature blockieren (sichtbares
  Signal → bewusst freigeben), aber **nie ein Loch öffnen**. Sicherheit ist so von
  „Mit-der-API-Schritt-halten" entkoppelt.

**Konkrete Maßnahmen für stabiles „Interface-Horchen":**

1. **Daten- statt code-getriebene Write-Allowlist:** die geprüften Endpoints als
   Konfigurationstabelle `{Methode, Pfad-Template, erforderliche Checks}` — Anpassen =
   Config-Edit + Review, kein Logikumbau.
2. **Feld-Extraktion statt Deep-Parsing:** je weniger vom Payload der Warden versteht,
   desto weniger kann brechen.
3. **Contract-Tests (§8) gegen die echte GitLab-API / deren OpenAPI-Spec:** ändert sich
   ein geprüfter Endpoint, wird ein Test rot — Drift fällt früh und automatisch auf,
   statt im Betrieb.
4. **Deprecation-Announcements abonnieren:** v4-Breaking-Changes kommen angekündigt mit
   Vorlauf; kein Überraschungsmoment.
5. **Layer-2-Backstop (§7):** verschiebt sich doch mal ein Feld und die Write-Policy
   greift falsch, fangen Push Rules / Protected Branches / fehlende Merge-Rechte den
   Worst Case GitLab-seitig ab.

**MCP davor — hilft das bei der Wartung?** Nein, nicht bei der sicherheitsrelevanten.
Wegen R6 muss der Warden **Token-Halter und Egress** bleiben, also an seinem unteren Rand
immer rohes GitLab-HTTP sprechen — die HTTP-Kopplung bleibt also bestehen, egal was
davor sitzt.

| Topologie | Was das MCP bringt | Wartungskopplung des Warden | R6 |
| --------- | ------------------ | --------------------------- | -- |
| **MCP als Agent-Ergonomie davor** (`Agent → MCP → Warden → GitLab`, MCP **ohne** echtes Token) | bequeme Tool-Oberfläche für den Agenten | unverändert — Warden poliziert weiter rohes HTTP | ✅ |
| **Warden spricht MCP-Tools, fremdes MCP hält Token** | verlagert API-Mapping ins MCP | — | ❌ Token im Fremd-Code = Grenze fällt |
| **Gepinntes MCP im Trust-Bereich hält Token** | API-Mapping „geschenkt" | du pinnst/auditierst jeden Bump eines großen, fremden Codebestands | ⚠️ größere, schlechter auditierbare Grenze |

**Fazit:** Das MCP bleibt **optionale Agent-Ergonomie** (darf nie den Token halten, vgl.
§6.3). Die Wartbarkeit gewinnt man **nicht** durch Auslagern der Grenze an ein fremdes
MCP, sondern durch das Warden-interne Design: Read-Pass-Through + datengetriebene
Write-Allowlist + Default-deny + Contract-Tests. Die echte Wartungslast ist damit „bei
einer angekündigten v4-Änderung an einem der ~5 geprüften Schreib-Endpoints eine
Config-Zeile + Test anpassen" — nicht „die GitLab-API nachbauen".

### 6.10 Leserisiko & Exfiltration (R1)

„Lesen auf alles" ist **keine** harmlose Regel — unter der adversarischen Prämisse ist
sie eine Exfil-Fläche so groß wie alles, was der Read-Token sieht: Quellcode, CI-Logs,
MR-Historie, Pipeline-Definitionen. Erschwerend: Selbst bei perfekt umzäuntem
Schreibpfad bleiben **verdeckte Ausgangskanäle** — Commit-Inhalte auf `claude/*`,
MR-Beschreibungen, Pipeline-YAML, plus der Research-Egress (§6.6). Ein wirklich
böswilliger Agent mit Vollzugriff aufs Lesen lässt sich **nicht vollständig** an
Exfiltration hindern.

Daher: **begrenzen, nicht behaupten, es sei gelöst.** Maßnahmen, gestaffelt:

- **Read-Scope bewusst klein halten.** `ALLOWED_PROJECTS` / die Projektmitgliedschaft des
  Read-Tokens definieren die Lese-Reichweite. Default = **nur die Projekte, an denen der
  Agent arbeitet**, nicht die ganze Gruppe. Das ist die wirksamste Einzelmaßnahme.
- **Egress-Allowlist eng** (§6.6) — beschränkt den bequemsten Exfil-Pfad.
- **Audit-Log** (§6.8) macht ungewöhnliche Lesemuster wenigstens nachträglich sichtbar.
- **Restrisiko akzeptieren & benennen:** verdeckte Kanäle über erlaubte Schreibziele
  (MR-Text, Commit-Inhalt) bleiben. Gegenmittel ist organisatorisch — derselbe
  menschliche MR-Review (R4), der Code-Poisoning fängt, sieht auch absurde MR-Inhalte.

**Konsequenz fürs Design:** R1 „auf alles" ist als **„auf alles im Arbeits-Scope"** zu
lesen. Ein wirklich projektübergreifendes Leserecht sollte eine bewusste, dokumentierte
Entscheidung sein, kein Default.

### 6.11 Ausfallverhalten des Warden

Der Warden ist der „Single Point of Trust" — sein Ausfallverhalten ist damit das
**wichtigste** zu spezifizierende Verhalten, nicht eine Randnotiz.

- **Fail-closed ist strukturell garantiert.** Weil `agent-net` `internal: true` ist
  (§6.1), hat der Agent **keine** Route zu `gitlab.com`. Fällt der Warden aus, verliert
  der Agent GitLab — er kann **nicht** auf einen Direktzugriff „durchfallen". Das ist der
  entscheidende Punkt: der gefährliche Fail-open-Fall ist per Netz-Topologie unmöglich,
  nicht per Konfiguration.
- **Health-Check & Restart:** Warden mit Healthcheck + `restart: unless-stopped`; der
  Agent (`git`/REST) bekommt bei Warden-Down klare Fehler (kein stiller Teil-Erfolg).
- **Quoten-State muss ausfall- *und* angriffssicher sein (R5):**
  - **Durabel:** SQLite mit WAL, auf einem Volume, `fsync` bei jeder Schreibaktion — ein
    Crash darf den Stundenzähler **nicht** verlieren.
  - **Fail-safe bei Verlust:** ist der State korrupt/leer und lässt sich nicht gegen die
    GitLab-Wahrheit rekonstruieren, gilt **„Limit erreicht" → Writes verweigern**, bis
    ein Reconcile (Zählung offener MRs/Branches per API) den echten Stand herstellt.
    **Niemals** „leer = 0 genutzt = alles frei" — sonst resettet ein Agent, der den Warden
    gezielt zum Absturz bringt, sein eigenes Rate-Budget (vgl. §9 / Roast).
  - **Reconcile beim Start:** offene Claude-MRs/Branches werden beim Hochfahren aus der
    API gezählt, bevor neue Writes zugelassen werden — die GitLab-Wahrheit dominiert den
    lokalen Zähler.
- **Idempotenz:** Der git-Push (G1) wird transparent durchgestreamt — annehmen und
  weiterleiten sind **derselbe** Vorgang, GitLab aktualisiert die Ref atomar, also kein
  „lokal akzeptiert, aber nicht weitergeleitet"-Spalt. Heikler ist der **API-Schreibpfad**
  (MR-Erstellung, Comments): Crasht der Warden zwischen Policy-Ok und GitLab-Call, darf der
  Retry nicht doppelt schreiben (Korrelations-ID, Vorab-Prüfung des Remote-Stands).

### 6.12 Lohnt sich ein MCP überhaupt noch?

Berechtigte Frage: Wenn der Warden die GitLab-REST-Schnittstelle ohnehin exponiert und
durchsetzt, und GitLab-native den Rest absichert — wozu dann ein MCP-Sidecar?

**Was das MCP *nicht* ist:**

- **Keine Sicherheitskomponente.** Der Warden poliziert jeden Request gleich, egal ob er
  aus einem MCP-Tool oder aus rohem `curl` kommt (§3, §6.3). Das MCP trägt **null** zur
  Durchsetzung bei.
- **Nicht funktional nötig.** Claude kann GitLab vollständig **ohne** MCP bedienen:
  `git` für clone/fetch/push, und die wenigen erlaubten Schreib-Operationen per REST
  (`curl`/`httpx`) gegen den Warden. Lesen sowieso über `git` + GET.

**Was das MCP *bringt* — reine Ergonomie:**

- **Typisierte Tool-Affordances:** benannte Tools mit Schema (`create_merge_request`,
  `post_note`, …) → der Agent muss REST-Aufrufe nicht aus dem Gedächtnis basteln.
- **Saubereres I/O:** geparste, kompakte Antworten statt roher `curl`-Ausgabe → weniger
  Token, weniger Parsing-Fehler.
- **Discoverability:** die Tool-Liste sagt dem Agenten, was möglich ist.

In der Praxis sind Agenten mit gut beschriebenen Tools oft zuverlässiger und
token-effizienter als mit Freiform-`curl`. Das ist der einzige reale Nutzen — **und er ist
hier klein:**

**Warum der Ergonomie-Vorteil bei GitLab gering ist:** Die **GitLab-REST-API v4 ist
stabil, exzellent dokumentiert und steckt praktisch im Trainingswissen jedes großen LLM.**
Claude kennt die relevanten Endpoints, Pfade und Payloads — `POST /projects/:id/
merge_requests`, `.../notes`, `.../pipeline` — meist auswendig. Das einzige MCP-Argument
(„der Agent muss Aufrufe nicht aus dem Gedächtnis basteln") trägt also kaum, weil das
Gedächtnis hier verlässlich ist. Ein knapper Spickzettel der erlaubten Aufrufe in
`CLAUDE.md` schließt die kleine Restlücke (genaue Warden-Basis-URL, das `claude/`-Präfix,
welche Writes erlaubt sind) günstiger als ein ganzer Sidecar.

**Kosten eines MCP:** ein zusätzlicher Sidecar-Container und — beim Fremd-MCP — eine
**churnende Drittabhängigkeit** auf dem Pfad zur Sicherheitsgrenze (§6.9).

**Empfehlung — Start ohne MCP, und das reicht vermutlich dauerhaft.** Claude nutzt `git`
+ eine in `CLAUDE.md` dokumentierte Handvoll erlaubter REST-Aufrufe gegen den Warden. Das
spart Container, Abhängigkeit und Churn-Quelle — bei minimalem Ergonomieverlust, weil die
GitLab-API dem Modell ohnehin geläufig ist. **Ein MCP nur nachrüsten, falls sich in der
Praxis messbar zeigt, dass der Agent mit rohem REST stolpert.** Falls doch, ist die bessere
Wahl ein **winziges Erst-Partei-MCP, das 1:1 die Warden-Allowlist abbildet** (bleibt in
eigener Wartungs-/Trust-Kontrolle, Tool-Oberfläche = Policy-Oberfläche) statt des breiten
Fremd-MCP. Kurz: **MCP ist eine optionale, später nachrüstbare UX-Optimierung — kein
Architektur-Baustein, von dem irgendetwas abhängt.**

---

## 7. Zweite Sicherungsschicht: GitLab-native Restriktionen

GitLab-seitige Restriktionen fangen den **Worst Case** auf, wenn der Warden versagt — Bug,
Fehlkonfig, Lücke im Filter. Wir gestalten die GitLab-Identität so, dass selbst ein
„durchgereichtes" Write-Token nur wenig Schaden anrichten kann.

**Reihenfolge-Hinweis (wichtig):** Obwohl hier als „Layer 2 / Backstop" geführt, ist diese
Schicht **kostenlos** (Zero-Code, nur GitLab-Konfiguration) und **robust** (von GitLabs
eigener Zugriffskontrolle durchgesetzt). Sie deckt **R1/R2/R4 nativ** ab und **R3
teilweise**. Deshalb wird sie laut Roadmap (§10) **zuerst** eingerichtet — *vor* dem
Warden. Was danach an eigenem Code übrig bleibt, ist im Wesentlichen **R5 (Quoten/Rate)**
und die **Ownership-Präzision von R3** (Developer darf nativ auf *jedem* MR kommentieren,
nicht nur auf den eigenen). Erst dieser Rest rechtfertigt den Warden — er muss sich gegen
die schon vorhandene native Schicht beweisen, nicht umgekehrt.

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
| R2    | Warden parst `receive-pack`-Ref-Kommandos: Präfix-Check (§6.2) | **Push Rules** Branch-Regex `^claude/` + Protected Branches        |
| R3    | API-Filter: Ownership + Endpoint-Allowlist                  | Developer-Rolle (kein Schreibrecht außerhalb eigener Branches)     |
| R4    | API-Filter: Merge-Endpunkte → 403                          | Protected Branches „merge: No one" + Approval-Regeln               |
| R5    | Quoten-/Rate-State (SQLite, Token-Bucket)                   | *(nativ nur grob über Plattform-Rate-Limits — Warden ist primär)* |
| R6    | Kein Token im Agenten + Netz-Isolation + Vertrauensgrenze   | Minimal-Scopes + Service-Account-Rolle begrenzen Worst-Case-Schaden |

**R5 ist die schwächste Stelle — und zugleich die am häufigsten auslösende Regel.** Sie
greift nicht nur bei Böswilligkeit, sondern weit eher **unbeabsichtigt**: Retry-Storm,
Bug-Loop, schlecht gescopter Prompt. Das verlangt einen **harten** Limit, und genau das
liefert der Warden: ein **synchroner** Pre-Write-Check, der inline blockt (§6.4) — *nicht*
der Cron. Ehrliche Einordnung der Schichten für R5:

- **Primär (hart, inline):** Warden-Hard-Limit mit durabler, fail-safe State-Haltung
  (§6.11). Das ist die eigentliche Durchsetzung.
- **Nativer Backstop (grob):** gitlab.com erzwingt **Plattform-Rate-Limits** (Requests pro
  Minute je User/IP) — fängt extremes Fluten ab, kennt aber **kein** „max. offene MRs/
  Branches". Für die Quoten-Regeln gibt es also keinen feinkörnigen nativen Backstop.
- **Cron-Watchdog (nachlaufend, *kein* echter Backstop):** zählt periodisch und
  alarmiert/schließt — reagiert per Definition *nach* der Tat und ersetzt den harten
  Limit nicht. Nur Sicherheitsnetz, kein Primärschutz.

Konsequenz: Auf der durabilität-/fail-safe-Auslegung des Warden-State (§6.11) liegt für
R5 das meiste Gewicht. Sie ist deshalb mit Priorität umzusetzen, nicht zuletzt.

---

## 8. Teststrategie

Die Regeln sind nur so viel wert wie ihr Nachweis. Ziel: **jede Regel R1–R6 hat
benannte, automatisierte Tests**, die in CI laufen — inklusive der bewussten
Umgehungsversuche aus §3.

### 8.1 Testebenen

| Ebene | Was | Werkzeug (Python) | Geschwindigkeit |
| ----- | --- | ----------------- | --------------- |
| **Unit** | Die reine Policy-Funktion `decide(request, state)` (§6.7): tabellengetriebene Fälle je Endpoint/Branch/Owner/State → erwartetes Allow/Deny + Regel | `pytest` (parametrize) | ms |
| **Integration (API)** | Warden gegen eine **Mock-GitLab-API**: Reads werden durchgereicht, Merge → 403, Ownership-Verletzung → Deny, Token wird **nie** an den Agenten geleakt | `pytest` + `respx`/`httpx.MockTransport` (oder WireMock) | < 1 s |
| **Integration (git)** | pkt-line-Parser gegen aufgezeichnete `receive-pack`-Bodies (erlaubte/verbotene Branchnamen, Delete = Null-OID, Multi-Ref); end-to-end: echte `git push` über den Warden gegen ein Wegwerf-Upstream → Accept/Reject (§6.2) | `pytest` + echtes `git` (`tmp_path`) | Sekunden |
| **Quoten/Rate** | N Writes treiben, (N+1) muss blocken; Sliding-Window-Ablauf mit **injizierbarer Uhr** (keine `sleep`-Tests) | `pytest` + Fake-Clock (`freezegun`/DI) | ms |
| **End-to-End / Red-Team** | `docker compose` mit Warden + Fake-GitLab + **„hostile agent"**-Container, der Bypässe durchspielt | `pytest` + docker / testcontainers | Minuten |

### 8.2 Red-Team-Suite (automatisiert)

Ein Container, der als böswilliger Agent agiert und gegen den **default-deny** anrennt;
jeder Test ist eine erwartete **Abwehr**:

- `printenv` / Dateisystem-Scan → assert: **kein** GitLab-Token auffindbar (R6).
- direkter Connect zu `gitlab.com` → assert: **keine Route** (Netz-Isolation, R6).
- Push auf `feature/x` (falsches Präfix) → assert: reject (R2).
- Force-Push / Delete auf fremden Ref → assert: reject (R2).
- `PUT .../merge` über jeden bekannten Endpoint/Alias → assert: 403 (R4).
- Kommentar/CI-Trigger auf fremdem MR → assert: deny (R3).
- Flooding: Branches/MRs/Writes über die Limits → assert: throttled/abgelehnt (R5).
- Exfil-Versuch zu nicht-allowlisteter Domain über den Forward-Proxy → assert: block (§6.6).

Diese Suite ist die ausführbare Form der Red-Team-Checks der Roadmap (§10) und sollte bei
jeder Warden-Änderung laufen.

### 8.3 Weitere Bausteine

- **Property/Fuzz** für den Pfad-Filter: zufällige Pfade dürfen den default-deny nie
  durchbrechen (`hypothesis`).
- **Coverage-Gate** auf der Policy-Funktion (z. B. ≥ 90 %), da sie sicherheitskritisch ist.
- **Regel-Traceability:** Testnamen tragen die Regel-ID (`TestR4_MergeBlocked`), damit
  die Abdeckung je Regel sichtbar ist.
- **Log-Assertions:** Tests prüfen, dass jede Entscheidung einen Audit-Eintrag mit
  korrekter Regel erzeugt und **kein** Token im Log steht (§6.8).

### 8.4 CI

GitLab-CI-Pipeline (`.gitlab-ci.yml`): **Unit + Integration + Quoten** bei jedem Push;
**E2E/Red-Team** bei MR und nächtlich (langsamer, braucht Docker). Pipeline rot →
Merge-Block. So wird die Sandbox selbst unter dem Schutz von Layer 2 entwickelt.

---

## 9. Restrisiken & offene Punkte

- **Warden ist Single Point of Trust.** Sein Code muss klein, auditierbar und gut
  getestet sein. Default-deny in beiden Pfaden (API & git).
- **Reconcile-Genauigkeit (R5):** lokaler Zähler vs. echte GitLab-Wahrheit muss
  periodisch abgeglichen werden (manuell geschlossene MRs, von außen gelöschte Branches).
- **git-Smart-HTTP-Proxy (§6.2, G1).** Deutlich schlanker als ursprünglich gedacht: kein
  bare-Mirror, kein `pre-receive`-Hook, kein Forwarder — nur pkt-line-Ref-Inspektion auf
  dem vorhandenen Warden. Restkomplexität: korrektes **Streaming** großer Packfiles und
  gzip-encodierte Bodies. Force-Push bleibt an GitLab Push Rules delegiert (G1-Grenze).
  Wer selbst das vermeiden will → Option D (Commit-API) als protokollfreier Fallback.
- **Push Rules / Protected-Branch-Semantik** auf gitlab.com vor Verlass im konkreten
  Setup verifizieren (UI-Pfade und genaue Wirkung können sich ändern).
- **Read-Token sieht alles, was es sehen darf** → eigene Analyse in §6.10 (Exfil-Fläche,
  begrenzt nicht eliminiert).
- **⚠️ Offener Verifikationspunkt — RC-Token-Fähigkeiten (§3.2):** Kann das Claude-OAuth-
  Token fremde Remote-Control-Sessions enumerieren/ansteuern? Vor Inbetriebnahme klären
  (Doku/Anthropic). Bis dahin gilt die strukturelle Prävention: dediziertes Konto +
  ggf. RC-loser Betrieb. Auch klären: ob ein Auth-injizierender Modell-Proxy mit RC/OAuth
  überhaupt möglich ist (§3.2) oder nur mit API-Key.
- **Warden ≈ Mini-GitLab?** Der Roast-Einwand zielte auf die naive Vollausbau-Variante
  (eigener git-Server: bare-Mirror + Hook + Forwarder). Der **transparente** Proxy (G1)
  entschärft das wesentlich: Er terminiert das git-Protokoll **nicht**, hostet kein Repo
  und reicht git-Edge-Cases (LFS, Submodule, Shallow-Clones, annotierte Tags) als opake
  Bytes durch — er muss sie nicht verstehen, nur den Ref-Kopf lesen. Restkopplung an die
  REST-API bleibt klein (§6.9). Gegenmittel insgesamt: Reihenfolge §10 (native Schicht
  zuerst, dann der schlanke G1-Proxy als SHA-erhaltender Schreibpfad).

### 9.1 Option D — warum die Commit-API hier *nicht* der Schreibpfad ist

Die Commit-API (Option D) wäre der protokollfrei einfachste Schreibpfad — **wenn** der
Agenten-Clone ephemer wäre. In diesem Setup ist er es nicht (Host bearbeitet denselben
Clone, §6), und der entscheidende Punkt unten — die garantierte SHA-Divergenz — macht D
zum Disqualifikator, nicht bloß zum „kein freien Tausch". Hier die vollständige
Kostenseite, auch als Begründung, warum **G1** (§6.2) den Vorzug bekommt.

- **Korrumpiert es das Repo? Nein — nicht strukturell.** GitLab baut den Commit
  serverseitig aus dem aktuellen Branch-Tip und aktualisiert die Ref atomar: gültiges
  Commit-Objekt oder sauberer Fehler, nie ein halb geschriebener Baum. Das Risiko ist
  **logisch, nicht strukturell** — falsch-aber-gültige Commits, kein kaputtes Repo.
- **Lokal ↔ remote divergiert garantiert.** `git push` lädt exakt den lokalen Commit
  (gleiche SHA) hoch; die Commit-API **konstruiert serverseitig einen neuen** Commit mit
  *anderer* SHA. Nach jedem Write ist die lokale Branch veraltet → `git fetch` Pflicht,
  Remote als Wahrheit. Wer naiv lokal weiter-committet und später reconcilen will,
  divergiert. Das ist die zentrale Disziplin, nicht ein Komfort-Detail.
- **Working-Tree → `actions[]`-Übersetzer.** „Was hat sich geändert" muss in die
  Action-Liste (`create`/`update`/`delete`/`move`/`chmod`, `text` vs. `base64`) übersetzt
  werden. Genau dort sehen Bugs wie Korruption aus: verpasster Rename → delete+create
  (History weg), Binary als `text` → mangled, vergessene Action → Datei fehlt still.
  Es ist ein Nachbau von `git add -A && git commit` und bricht an Renames, Mode-Bits,
  Symlinks, Binaries.
- **Stale-Base / Lost-Update.** Die API committet auf den *aktuellen* Tip;
  `update`/`delete`/`move`/`chmod` akzeptieren optional `last_commit_id` als
  Per-Datei-Optimistic-Lock — leicht vergessen. Ohne ihn überschreibt ein gegen alten
  Stand gebauter Commit still eine nebenläufige Änderung. Für einen Solo-Agenten auf
  eigenem `claude/*` harmlos, ein Footgun sobald sonst jemand dort schreibt.
- **`force: true` existiert auch an der API** → API-level Force-Push. „API-only" erfüllt
  R2 damit **nicht** automatisch; Warden bzw. Push Rules müssen `force: true` weiterhin
  ablehnen.
- **Ganze git-Operationsklassen überleben nicht:** Merges/Rebases/Cherry-Picks/Amends
  bilden sich nicht sauber ab; lineare lokale Commits lassen sich als je ein API-Call in
  Reihe wiederholen, Merge-Commits werden geplättet. Submodule/LFS praktisch raus, viele/
  große Dateien → große base64-JSON-Payloads.

**Netto:** Für einen *ephemeren* Agenten-Clone (vorwärts-nur auf `claude/*`, MR öffnen,
Clone wegwerfen) wäre Option D tragfähig. Sobald aber — wie hier — ein **Mensch denselben
Clone host-seitig bearbeitet**, ist die garantierte SHA-Divergenz nicht akzeptabel: Der
geteilte Clone läuft dauerhaft gegen den Server auseinander. Plus der Übersetzer, dessen
Bugs wie Korruption aussehen. Deshalb fällt die Wahl auf **G1 (§6.2)**; D bleibt nur
dokumentierter Fallback für den ephemer-Clone-Sonderfall.

---

## 10. Umsetzungs-Roadmap

Leitprinzip der Reihenfolge: **erst die kostenlose, robuste Schicht, dann nur den Rest in
Code.** Den teuren Eigenbau (Warden) erst bauen, wenn gemessen ist, was die GitLab-native
Schicht *nicht* abdeckt — nicht zuerst auf das Produkt committen.

1. **Token-Leak schließen (sofort):** `GITLAB_API_TOKEN` aus dem `claude-dev-env`-Service
   in `docker-compose.yml` entfernen; `GITLAB_GIT_TOKEN` aus dem Agenten ziehen. Kein
   echtes GitLab-Token mehr in der Agent-Env. (Behebt die akute R6-Verletzung aus §4.)
1b. **Dediziertes Claude-Konto (sofort):** Sandbox auf ein Wegwerf-/Service-Konto mit
   Budget-/Scope-Grenze umstellen, nicht das Primärkonto mounten (§3.2). Wichtigste
   Blast-Radius-Reduktion und unabhängig vom Rest umsetzbar.
2. **GitLab-native Schicht zuerst (§7), Zero-Code:** Service Account + Developer-Rolle,
   Protected Branches, Push Rules `^claude/`, Merge-Sperre, Protected CI-Variablen, Audit
   Events. Ergebnis: **R1/R2/R4 nativ durchgesetzt, R3 teilweise.**
3. **Messen, was übrig bleibt.** Verifizieren, dass die native Schicht hält; übrig
   bleiben im Wesentlichen **R5 (Quoten/Rate)** und **R3-Ownership** (kein
   Fremd-MR-Kommentar). Nur dafür wird Code gebaut.
4. **Netz-Isolation + Research-Egress:** `agent-net` `internal: true`; Forward-Proxy
   (Squid) mit Domain-Allowlist; `http(s)_proxy`/`no_proxy` im Agenten (§6.6). Sichert
   zugleich Fail-closed (§6.11).
5. **Read-Scope eng setzen (§6.10):** `ALLOWED_PROJECTS` = nur Arbeits-Projekte.
6. **Warden-Gerüst:** Stack-Entscheidung (§6.7); reine Policy-Funktion + Default-deny;
   Audit-Logging (§6.8); Ausfall-/Fail-safe-Semantik (§6.11); Unit-Test-Harness (§8) von
   Anfang an.
7. **Warden — Lesepfad:** Read-Token, GET-Pass-Through (REST) + git-`upload-pack`-
   Durchreichen; Agent (`git`/`curl`) auf den Warden umstellen. R1 verifizieren.
8. **Warden — git-Schreibpfad (G1, §6.2):** transparenter Smart-HTTP-Proxy mit pkt-line-
   Ref-Inspektion (Präfix, Delete-Block, Branch-Zahl, Rate). **SHA-erhaltend**, damit der
   host-seitige Clone kohärent bleibt — kein Mirror/Hook/Forwarder. R2/R5 (Push)
   verifizieren, inkl. echtem `git push` vom Host-Clone.
9. **Warden — API-Schreibpfad (MRs/Comments/CI):** R3-Ownership-Checks, Merge→403,
   R5-Hard-Limit mit durablem State. R3/R4/R5 verifizieren.
10. **Transparenz-Ausbau:** Log-Viewer bzw. Grafana/Loki (§6.8).
11. **Red-Team-Suite (§8.2) grün:** alle Umgehungsversuche automatisiert abgewehrt; in CI
    verankert (§8.4).

---

## 11. Referenz-Deployment (`docker-compose` + `config/`)

Die Detailpläne zeigen jeweils nur ihren Ausschnitt. Hier steht die **konsolidierte,
vollständige** Zielkonfiguration als eine Stelle, die alles zusammenführt — der Stand
**nach Stufe 02** (Warden + Forward-Proxy aktiv, kein MCP-Sidecar, §6.12).

### 11.1 Prinzip: drei Arten von Konfiguration, sauber getrennt

| Art | Wohin | Beispiele | Sichtbar für |
| --- | ----- | --------- | ------------ |
| **Secrets** | `.env` (gitignored, **nie** committen) | `GITLAB_READ_TOKEN`, `GITLAB_WRITE_TOKEN`, `ANTHROPIC_API_KEY` | nur der jeweilige Service (Warden bzw. Agent) |
| **Host-editierbare Tunables** | **`config/`** (read-only gemountet) | Allowlist, Branch-Präfix, Limits, erlaubte Projekte, `squid.conf` | Warden / Proxy (read-only), **keine** Secrets |
| **Laufzeit-State** | **Bind-Mounts** neben dem Compose-File (`./state/`, `./logs/`) | SQLite-Quoten-State, Audit-Logs, Egress-Log | nur der schreibende Service; **vom Host direkt einsehbar** |

**Kernregel:** In `config/` liegt **nie ein Geheimnis**. Der Ordner ist bewusst
host-editierbar (der Nutzer pflegt Allowlist & Limits direkt von außen, z. B. in VSCode)
und wird **read-only** in die Container gemountet — beides verträgt sich nur, wenn dort
keine Tokens stehen. Secrets kommen ausschließlich aus `.env` in die Env des **berechtigten**
Service (Tokens nur in den Warden, nie in den Agenten — R6, §3).

### 11.2 On-Disk-Layout neben dem Compose-File

**Alles liegt als gewöhnliche Dateien/Ordner neben der `docker-compose.yml`** — keine
Docker-Named-Volumes. So lassen sich Config, State und Logs mit normalen Werkzeugen
(`cat`, `tail -f`, `grep`, `git`, ein Editor) **ohne Docker-Tools** einsehen und
auditieren:

```
<compose-dir>/
├── docker-compose.yml
├── .env                     # Secrets (gitignored)            → §11.3
├── config/                  # read-only gemountet, host-editierbar (KEINE Secrets)
│   ├── allowlist.txt        #   Forward-Proxy: erlaubte Domains
│   ├── squid.conf           #   Forward-Proxy: Squid-Konfiguration (default-deny, SNI-peek)
│   └── warden.toml          #   Warden: Präfix, Limits, erlaubte Projekte (W10)
├── workspace/               # Agenten-Working-Clone — auch host-seitig editiert (VSCode), §6
├── claude/                  # Claude-Home (nur Sandbox-Credential, §3.2)
├── state/                   # Laufzeit-State (read-write Bind-Mount)
│   └── warden/              #   SQLite-Quoten-State (state.db + WAL), durabel (§6.11)
└── logs/                    # Audit-Logs (read-write Bind-Mount) — direkt tailbar
    ├── warden/              #   warden-audit.jsonl (+ gerendertes .txt), §6.8
    └── squid/               #   access.log (Egress-Audit, §6.6)
```

- **`config/`** sind die **einzigen** Dateien, die der Nutzer im Normalbetrieb editiert.
  Änderungen wirken ohne Image-Rebuild: Allowlist/Squid per `squid -k reconfigure`,
  `warden.toml` per Warden-Neustart. Inhalte in den Detailplänen:
  [`allowlist.txt` + `squid.conf`](./02-forward-proxy/03-squid-config.md),
  [`warden.toml`](./02-warden.md) (W10).
- **`state/` und `logs/`** schreibt nur der jeweils berechtigte Service; für den Menschen
  sind sie **read-only-Lektüre** (das Audit-Log nie von Hand ändern — §6.8). Die Container
  haben read-only Root-FS und schreiben **ausschließlich** in diese Bind-Mounts (+ tmpfs).
- **Anlegen vor dem ersten Start:** Die Ordner `state/warden`, `logs/warden`, `logs/squid`
  müssen existieren und dem Container-User (`DEV_UID`) gehören, sonst legt Docker sie als
  `root` an und der non-root-Service kann nicht schreiben (§11.6).

### 11.3 `.env` (Secrets — gitignored)

```dotenv
# Anthropic — dediziertes Sandbox-Konto (§3.2), NICHT das Primärkonto
ANTHROPIC_API_KEY=

# GitLab — NUR der Warden bekommt diese (nie der Agent, R6)
GITLAB_READ_TOKEN=      # read_api, read_repository
GITLAB_WRITE_TOKEN=     # api (Service-Account/Developer, §7)

# Host-Pfade
CLAUDE_HOME=./claude
PROJECT_DIR=./workspace
```

### 11.4 Vollständige `docker-compose.yml`

```yaml
# Zielzustand nach Stufe 02. Secrets via .env, Tunables via ./config (read-only),
# State/Logs via Bind-Mounts neben dem Compose-File (./state, ./logs) → ohne Docker-Tools
# auditierbar. Agent hält KEIN GitLab-Token (R6).

services:
  # ── Agent ────────────────────────────────────────────────────────────────
  claude-dev-env:
    build:
      context: .
      dockerfile: Dockerfile
      args:
        DEV_UID: ${DEV_UID:-1000}
        # weitere Build-Args wie gehabt (UV/CLANG/RUST/CONAN/NODE/CLAUDE_CODE_VERSION)
    networks: [agent-net]                       # NUR agent-net → keine Internet-/GitLab-Route
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}  # dediziertes Sandbox-Konto (§3.2)
      - GITLAB_API_URL=http://gitlab-warden:8080/api/v4   # REST über den Warden
      - http_proxy=http://forward-proxy:3128    # Research/Build-Egress (§6.6)
      - https_proxy=http://forward-proxy:3128
      - no_proxy=gitlab-warden                  # GitLab läuft über den Warden, nicht den Proxy
      # KEIN GITLAB_API_TOKEN / GITLAB_GIT_TOKEN (R6, §4)
    volumes:
      - ${CLAUDE_HOME:-./claude}:/home/dev/.claude     # nur Sandbox-Credential (§3.2)
      - ${PROJECT_DIR:-./workspace}:/workspace         # host-seitig editierbar (VSCode), §6
    working_dir: /workspace
    tty: true
    stdin_open: true
    restart: unless-stopped
    depends_on:
      gitlab-warden:  { condition: service_healthy }
      forward-proxy:  { condition: service_healthy }

  # ── Warden (einzige Vertrauensgrenze, hält ALLE GitLab-Tokens) ────────────
  gitlab-warden:
    build: ./warden
    networks: [agent-net, egress-net, admin-net]
    environment:                                # Secrets NUR hier, aus .env
      - GITLAB_READ_TOKEN=${GITLAB_READ_TOKEN}
      - GITLAB_WRITE_TOKEN=${GITLAB_WRITE_TOKEN}
    volumes:
      - ./config/warden.toml:/etc/warden/warden.toml:ro   # host-editierbar, KEINE Secrets
      - ./state/warden:/var/lib/warden                    # SQLite-Quoten-State (durabel, §6.11)
      - ./logs/warden:/var/log/warden                     # Audit-JSONL (§6.8)
    read_only: true
    tmpfs: [/tmp]
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9090/healthz')"]
      interval: 5s
      timeout: 3s
      retries: 10
    restart: unless-stopped

  # ── Forward-Proxy (Research/Build-Egress, hält KEINE Credentials) ─────────
  forward-proxy:
    build: ./forward-proxy                      # squid mit SSL-Support
    networks: [agent-net, egress-net]
    volumes:
      - ./config/squid.conf:/etc/squid/squid.conf:ro      # host-editierbar
      - ./config/allowlist.txt:/etc/squid/allowlist.txt:ro
      - ./logs/squid:/var/log/squid                       # Egress-Audit (§6.6)
    read_only: true
    tmpfs: [/var/spool/squid, /tmp]
    healthcheck:
      test: ["CMD", "squidclient", "-h", "127.0.0.1", "mgr:info"]
      interval: 5s
      timeout: 3s
      retries: 10
    restart: unless-stopped

networks:
  agent-net:  { internal: true }   # KEIN Egress — Agent erreicht nur Warden + Proxy
  egress-net: {}                   # Internet — nur Warden & Proxy
  admin-net:  {}                   # Healthz/Log-Viewer (Port 9090) — kein Agent

# Kein top-level `volumes:`-Block — alle Mounts sind Bind-Mounts auf ./config, ./state,
# ./logs, ./workspace, ./claude (siehe §11.2), damit alles ohne Docker-Tools auditierbar ist.
```

### 11.5 Warum diese Topologie die Regeln trägt (Kurzbezug)

- **R6 / Credential-Isolation:** Tokens stehen nur in der Warden-Env (`.env`), nie beim
  Agenten; `agent-net` ist `internal: true` → der Agent hat **keinen** Netzweg an
  `gitlab.com` vorbei am Warden (§3, §6.11). Fail-closed ist strukturell.
- **`config/` read-only + ohne Secrets:** host-editierbare Policy (Allowlist, Limits,
  Präfix) ohne Image-Rebuild, ohne je ein Geheimnis preiszugeben (§11.1).
- **Kein MCP-Sidecar:** der Agent spricht `git` + REST direkt gegen den Warden (§6.12).
- **Host-Workspace:** `workspace/` bind-gemountet → VSCode am Host teilt den Clone; der
  Schreibpfad ist SHA-erhaltend (G1, §6.2), nicht die Commit-API.
- **Auditierbarkeit ohne Docker-Tools:** Config, State und Logs liegen als gewöhnliche
  Dateien neben dem Compose-File (§11.2). Audit-Log lesen = `tail -f logs/warden/warden-audit.jsonl`,
  Egress prüfen = `grep logs/squid/access.log`, Quoten-State inspizieren = `sqlite3
  state/warden/state.db` — alles ohne `docker volume inspect`/`docker cp`. Das Audit-Log
  ist Quelle der Wahrheit (§6.8); die Bind-Mounts machen es unmittelbar greifbar.

### 11.6 Verzeichnisse anlegen & Berechtigungen

Bind-Mounts existieren als echte Host-Ordner — sie müssen **vor dem ersten Start**
angelegt sein und dem Container-User gehören, sonst legt Docker sie als `root` an und der
non-root-Service (read-only Root-FS, `DEV_UID`) kann **nicht** in `state/`/`logs/`
schreiben:

```bash
mkdir -p config state/warden logs/warden logs/squid workspace claude
chown -R "${DEV_UID:-1000}" state logs            # Schreibziele dem Service-User geben
# config/ bleibt dem Host-Editor; wird ohnehin read-only gemountet
```

`config/` ist read-only gemountet (Schreibschutz strukturell), `state/` und `logs/`
read-write nur für den jeweiligen Service. `git`-seitig: `state/` und `logs/` gehören in
`.gitignore` (Laufzeitdaten), `config/` wird **versioniert** (Policy-Artefakt), `.env`
ist gitignored (Secrets).

> **Bezug zur Bestandsaufnahme (§4):** Diese Compose ersetzt die heutige
> MCP-Sidecar-Variante, die `GITLAB_API_TOKEN` in den Agenten reicht. Der Übergang ist
> gestaffelt: Stufe 01 zieht zuerst nur die Tokens
> ([`01-bootstrap-hardening.md`](./01-bootstrap-hardening.md)), Stufe 02 stellt auf obige
> Zieltopologie um.
