# Roast-Iteration 4 — Lokaler Modus (§11): Kritik & Antworten

Gegenstand: der **neu hinzugefügte §11 „Lokaler Modus"** in
[`05-repackaging.md`](./05-repackaging.md) — `catraz local` als drop-in `claude` mit
Sandbox-Netz. Eine Roast-Runde, wie beauftragt.

> **Hinweis zum Ablauf:** Der für diese Runde gestartete Roast-Subagent lief in das
> Session-/Usage-Limit (kein verwertbarer Output). Statt einen weiteren teuren Spawn im
> limitierten Kontext zu riskieren, wurde der adversariale Review **inline** mit derselben
> Härte geführt (gesamter Kontext lag vor). Befunde, Schweregrad und Antworten unten —
> dasselbe Format wie Runden 1–3.

---

## #1 — Lokaler Modus ist ein neuer Agent-Start-Pfad, der die Always-On-Sicherheits-Preflight umgehen kann; Risiko des stillen *un*sandboxed-Durchfalls · BLOCKER · Sicherheit

**Befund:** Das Erstkonzept ließ den Preflight nur auf dem kalten Auto-Up laufen
(„healthy → skip"). Damit liefe `catraz local` bei warmer Infra **ohne** Re-Check — ein
nachträglich manipulierter `compose.override.yml` (der die Grenze auflöst, §4.4) würde nicht
gefangen. 04-cli §5.3 verlangt aber „security-Checks laufen *immer*" bei jedem Agent-Start —
und `local` *ist* ein neuer Agent-Start. Zweitens: ein Alias `claude='catraz local'`, der
außerhalb eines `.catraz`-Projekts **still das Host-`claude`** exect, wäre das
schlechtest­mögliche Ergebnis (Nutzer glaubt sich sandboxed, ist es nicht).

**Antwort: angenommen — der wichtigste Fix der Runde.** **Änderung (§11.3):** (a) der
**aufgelöste-Compose-Invariantencheck (§4.4)** läuft vor **jedem** `local`-Aufruf (billig,
ein `docker compose config`-Parse, **nicht** gecacht); die teuren Online-Proben nur auf dem
kalten Pfad. (b) **Fail-closed**: kein `.catraz` → Fehler + Hinweis `catraz init`, **niemals**
Host-`claude`.

## #2 — Falsches Sicherheitsgefühl: der Alias suggeriert mehr Schutz als da ist · MAJOR · Transparenz/Sicherheit

**Befund:** „Safety net always on" verleitet zu glauben, lokaler Modus mache Claude
*ungefährlich*. Tatsächlich behält der Agent die Claude-Credentials (irreduzibel), kann über
*erlaubte* Domains exfiltrieren und den gebundenen Working-Tree frei editieren. Ohne klare
Abgrenzung ist der Alias Sicherheits-Theater.

**Antwort: angenommen.** **Änderung:** Neuer **§11.8 „Was schützt, was nicht"** mit
Schütztt/Schützt-nicht-Tabelle (Netz/Git ja; Working-Tree, Exfil-über-Allowlist, Credentials
nein — *identisch* zum Remote-Modus). `catraz local --help` sagt es im Klartext.

## #3 — „Normale Prompts schützen die Dateien" ist im eigenen Threat-Model falsch · MAJOR · Sicherheit/Ehrlichkeit

**Befund:** Das Erstkonzept rahmte §11.6 so, als wären normale Permission-Prompts der Schutz
für `/workspace`. Aber Prompts sind **Client-UI**, keine Durchsetzung — ein als bösartig
angenommener/prompt-injizierter Claude ignoriert sie. Gegen das Modell, das das Projekt
explizit annimmt, schützen Prompts nichts.

**Antwort: angenommen.** **Änderung:** §11.6 stellt klar: Prompts sind **Verhaltens-Parität**
zum echten `claude` (UX für den *ehrlichen* Fall), **nicht** der Sicherheits-Mechanismus —
der steht in §11.8 (Warden/Squid). Prompts bleiben als Default (nativer Feel), werden aber
nicht mehr als Datei-Schutz verkauft.

## #4 — `argparse.REMAINDER` kollidiert mit catraz' globalen Flags → Alias reicht nicht sauber durch · MAJOR · Anwenderfreundlichkeit

**Befund:** Die globalen Flags (`-C`, `--no-color`, …) wirken laut 04-cli „vor *oder* nach"
dem Subcommand. Dann fräße `catraz local --no-color …` das Flag für catraz statt für claude —
der drop-in-Alias bräche bei jeder `claude`-Form, die zufällig ein catraz-Flag trägt.

**Antwort: angenommen.** **Änderung:** §11.3 — `local` **erbt die globalen Flags nicht**;
alles nach `local` gehört `claude`, catraz-Optionen stehen *vor* `local`
(`catraz -C <dir> local …`). Reiner REMAINDER-Durchgriff.

## #5 — Scope-Creep & stille `up`-Re-Definition · MAJOR · Einfachheit/Kohärenz

**Befund (a):** Zwei Modelle + Profil + entrypoint-Branch + lazy-up + `--warm` + `--yolo` —
`--warm` ist ein *zweites* Ausführungsmodell (`exec` statt `run`) auf Verdacht.
**Befund (b):** §11.4 definiert `catraz up` zu „nur Infra" um, obwohl 04-cli §5.3/§10 sagen
„up startet alle drei" — unbenannt.

**Antwort: beides angenommen.** **Änderung:** `--warm` **aufgeschoben** (§11.7 — erst wenn
Latenz real stört; kein zweites Modell auf Verdacht, die Einfachheits-Lehre aus Runden 1–3).
Die `up`-Re-Definition ist nun **explizit** als bewusste Änderung markiert (§11.4) und zieht
als **Rollout-Schritt 6** in §10 ein.

## #6 — `docker compose run` und das Verhalten gegenüber laufenden Deps · MAJOR · Sicherheit/Einfachheit

**Befund:** Das Konzept verließ sich darauf, dass `run` die laufende Infra „über `depends_on`
nutzt". `run`s Umgang mit `depends_on`/Health ist versionsabhängig und kann Deps
neu-/anstarten → Race, oder Agent-Start *vor* Warden-Health (fail-open-Fenster).

**Antwort: angenommen.** **Änderung:** §11.3 — catraz stellt die Infra-Gesundheit **selbst**
sicher (Schritt 3) und ruft dann `run --rm --no-deps agent` — deterministisch, kein
dep-Restart-Race, kein Verlass auf `run`-Interna.

## #7 — `run`-Flag-Override-Fläche · MAJOR · Sicherheit

**Befund:** `docker compose run` akzeptiert `--network`, `--privileged`, `--volume`,
`--entrypoint`, `--user` — würden Passthrough-Argumente vor das `--` lecken, ließe sich die
Isolation schwächen.

**Antwort: angenommen.** **Änderung:** §11.3 — catraz exponiert auf `local` **keines** dieser
Flags; Claude-Argumente stehen strikt nach `--`/im REMAINDER und werden nie als
`run`-Flags interpretiert; `run` instanziiert die **unveränderte** Service-Definition (Netze,
Shadow-Mount, RO-Home, kein Token).

## #8 — Entrypoint-Kohärenz mit §6.4 · MINOR · Kohärenz

**Befund:** §11.5 fügt einen `local`-Exec-Branch hinzu; muss klar *derselbe* entrypoint +
dasselbe Setup wie §6.4 sein, nur anderer finaler Exec. Außerdem sind die RC-spezifischen
JSON-Patches (`remoteDialogSeen`, `bypassPermissionsModeAccepted`) im `local`-Modus unnötig.

**Antwort: angenommen.** **Änderung:** §11.5 verweist explizit auf das §6.4-Setup und macht
die RC-Patches **modus-abhängig** (nur Daemon-Pfad).

## #9 — Auth im lokalen Pfad · MINOR · Anwenderfreundlichkeit

**Befund:** `catraz local` bei `AUTH_MODE=subscription` mit abgelaufener Credential würde
opak scheitern.

**Antwort: angenommen.** **Änderung:** §11.3 Schritt 3 bindet den `AUTH_MODE`-Check (§6.2)
ein: fehlende Subscription-Credential → Auto-`sync` oder klarer Fehler.

---

## Was am Erstkonzept RICHTIG war (unverändert)

- **Persistente Infra + ephemerer Agent** (`run --rm` je Aufruf) — trifft „Container nur bei
  Aufruf neu starten" *und* ist sicherer (kein Zustand zwischen Aufrufen im bösartigen
  Container). Kern-Idee, bleibt.
- **Profil-Split Infra ↔ Daemon** — saubere Trennung, beide Modi teilen die Infra.
- **Wiederverwendung der gesamten §3/§4/§6-Maschinerie** (Shadow-Mount, Auth, RO-Home)
  *unverändert* — `run` instanziiert dieselbe Service-Definition; lokaler Modus erfindet
  keine zweite Sicherheitsmechanik.
- **Das Alias-Ziel selbst** — ein drop-in `claude` mit immer-an Netz/Git-Netz ist echter
  Mehrwert; nur die *Ehrlichkeit* darüber, *welches* Netz (§11.8), musste nachgeschärft.
- **Workdir-Mapping / TTY / Exit-Pass** — der native Feel, korrekt.

## Netto nach Iteration 4

Der lokale Modus ist im Kern tragfähig und fügt sich ohne neue Sicherheitsmechanik ein — er
*fährt* denselben Käfig nur anders. Die Runde schloss einen echten **BLOCKER** (Preflight-
Umgehung + stiller un-sandboxed-Durchfall → jetzt immer-Invariantencheck + fail-closed) und
korrigierte die **Ehrlichkeit** (Prompts sind UX, nicht Schutz; §11.8 sagt klar, was das Netz
leistet). Scope wurde *zurückgenommen* (`--warm` aufgeschoben), nicht erweitert — konsistent
mit der Lehre der ersten drei Runden. Damit steht §11 auf demselben Reifegrad wie der Rest
des Plans.
