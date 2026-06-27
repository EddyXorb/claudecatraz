# 05 — Repackaging: `catraz` als installierbares Werkzeug

> **Leitsatz:** `catraz` soll sich anfühlen wie `git` — einmal installiert, in
> **jedem** Ordner aufrufbar, und alles, was es dort anlegt, versteckt es in einem
> einzigen `.catraz/`-Verzeichnis. Das Repo ist dann **Programmquelle**, nicht mehr
> der Ort, an dem man arbeitet.

Status: ⏳ geplant. Voraussetzung: Stufen 01/02 implementiert (Warden, Forward-Proxy,
CLI-Grundgerüst stehen). Dies ist ein **Querschnitt-Refactoring** — es ändert nicht das
Sicherheitsmodell (R1–R6 bleiben), sondern *wie* das Projekt verpackt, verteilt und in
einen Zielordner eingebracht wird.

Dieses Dokument adressiert die sieben Punkte aus [`TODO.md`](../../../TODO.md) als ein
zusammenhängendes Bild statt als sieben Einzelflicken — sie hängen alle am selben Hebel.

---

## 1. Ausgangslage — warum das ein Refactoring und kein Patch ist

Heute ist claudecatraz ein **„clone-and-run-in-place"-Projekt**: man klont das Repo an
genau den Ort, an dem der Stack laufen soll, editiert Dateien im Repo-Root, und mischt
dabei drei Dinge, die nichts miteinander zu tun haben:

| Im Repo-Root liegt heute … | … ist aber eigentlich | Problem |
| -------------------------- | --------------------- | ------- |
| `Dockerfile`, `entrypoint.py`, `AGENT.md`, `warden/`, `forward-proxy/`, `docker-compose.yml` | **Programm** (versioniert, unveränderlich) | Dominiert die Sicht, verwirrt Einsteiger (TODO 3, 4) |
| `config/warden.toml`, `allowlist.txt`, `squid.conf` | **Konfiguration** (host-editierbar) | im selben Topf wie das Programm |
| `.env`, `claude/`, `state/`, `logs/`, `workspace/` | **Laufzeitdaten** (pro Einsatz) | liegen offen im Root, kein klarer „mein Kram"-Ort (TODO 6) |

Daraus folgen alle sieben TODO-Punkte. Sie sind **eine** Designfrage in sieben
Erscheinungsformen: *Wie trennt man Programm von Laufzeit so sauber, dass das Werkzeug
von überall läuft, sich unauffällig in einen bestehenden Ordner einnistet und dabei den
Agenten nicht den eigenen Maschinenraum lesen lässt?*

### 1.1 Die sieben Punkte → Lösungs-Matrix

| # | TODO | Kern-Lösung | Abschnitt |
| - | ---- | ----------- | --------- |
| **1** | sync ⊻ API-Key, eines automatisch aktiv; Quell-`CLAUDE_HOME` aus `.env` | **`auth_mode`** (`subscription`\|`api_key`) — `doctor` erzwingt *genau eines*, `up` aktiviert es automatisch | §6 |
| **2** | beliebige Dockerfiles laufen lassen, ohne den Claude-Layer-Komfort zu verlieren | **Image-Schichtung**: `BASE_IMAGE` (mitgebracht) + catraz-eigener **Claude-Layer** `FROM ${BASE_IMAGE}` | §5 |
| **3** | `entrypoint.py` nicht im Root | wird **Paket-Asset** (`src/catraz/container/entrypoint.py`), ins Image gebacken | §3, §4 |
| **4** | `AGENT.md` als „Asset" verschwinden lassen | wird **Paket-Asset** (`src/catraz/assets/AGENT.md`), ins Image gebacken | §3, §4 |
| **5** | von überall lauffähig, einmal klonen + `uv` installieren | **`uv tool install`** mit Console-Entry-Point `catraz` | §3 |
| **6** | alles in `.catraz/` verstecken; Programm/Laufzeit trennen | **`.catraz/` als einziges Laufzeit-Heim** im Zielordner | §4 |
| **7** | catraz in bestehenden Ordner einnisten; Agent darf `.catraz/` **nicht** lesen, den Rest schon | **Shadow-Mount**: Projekt nach `/workspace` binden, `/workspace/.catraz` mit leerem tmpfs überdecken | §4.3 |

---

## 2. Zielbild

```bash
# einmalig, irgendwo:
git clone https://…/claudecatraz && uv tool install ./claudecatraz
#   → `catraz` liegt jetzt auf dem PATH, global. Das Repo darf danach weg.

# in JEDEM Projektordner, den man sandboxen will:
cd ~/work/some-existing-repo
catraz init          # legt ./.catraz/ an (Config, Secrets, Claude-Home, State, Logs)
catraz up            # baut/startet den Stack, /workspace = dieser Ordner (ohne .catraz)
```

Nach `init` sieht der Zielordner so aus — **eine** neue Sache, sonst nichts:

```
some-existing-repo/
├── .catraz/                 ← alles von catraz, sonst unangetastet
├── src/ …                   ← das eigentliche Projekt (der Agent arbeitet hier)
└── (.gitignore  += .catraz/)
```

Im Container sieht der Agent:

```
/workspace/                  ← bind-mount von some-existing-repo/
├── src/ …                   ← les-/schreibbar (er soll ja arbeiten)
└── .catraz/                 ← LEER (tmpfs-Overlay) — der echte Inhalt ist unsichtbar
```

Das ist die Auflösung der im TODO formulierten Sorge *„ein Bind-Mount auf den aktuellen
Ordner, der selbst den `.catraz`-Ordner enthält, erscheint mir gewagt"*: Man mountet den
ganzen Ordner — **neutralisiert aber die heikle Stelle**, indem man sie im Container
überdeckt (§4.3).

---

## 3. Distribution — von „Repo am Einsatzort" zu „installiertes Werkzeug" (TODO 5, 3, 4)

### 3.1 Mechanismus: Python-Paket mit Console-Entry-Point

`catraz` ist heute schon reine Standardbibliothek-Python (ein 858-Zeilen-`./catraz`).
Der Schritt zum installierbaren Werkzeug ist klein:

```toml
# pyproject.toml (neu, im Repo-Root)
[project]
name = "claudecatraz"
version = "0.2.0"
requires-python = ">=3.11"        # tomllib, bereits vorausgesetzt

[project.scripts]
catraz = "catraz.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["src/catraz"]
# Assets UND Build-Kontexte MÜSSEN ins Wheel — sonst kann nach der Installation kein
# Image gebaut werden. warden/ und forward-proxy/ sind Build-Kontexte und ziehen mit
# unter assets/ (Roast-1 #9: sonst fehlen sie im Wheel und der Stack baut nicht):
[tool.hatch.build.targets.wheel.force-include]
"src/catraz/assets"    = "catraz/assets"
"src/catraz/container" = "catraz/container"
"warden"               = "catraz/assets/warden"
"forward-proxy"        = "catraz/assets/forward-proxy"
```

```bash
uv tool install ./claudecatraz      # oder: pipx install ./claudecatraz
catraz --version
```

`catraz` lebt danach in einem isolierten venv und liegt als Shim auf dem PATH.
Das geklonte Repo wird **nicht mehr gebraucht** (außer für Updates: `uv tool upgrade`).

#### Assets als stabiler Cache, nicht als venv-Pfad (Roast-1 #9)

Docker-Build-Kontexte **dürfen nicht** auf Pfade *im uv-/pipx-venv* zeigen: deren Layout
ist keine stabile API, `uv tool upgrade` darf sie verschieben, und zip-/zipapp-Installs
haben gar keinen Dateisystempfad. Darum extrahiert `catraz` seine Assets beim ersten
Gebrauch **deterministisch** (nicht „ggf.") in einen versionierten Cache:

```
~/.cache/catraz/<version>/{compose,claude-layer,bases,warden,forward-proxy,config,…}
```

Alle Compose-`build.context`- und `docker build`-Aufrufe zeigen auf diesen Cache —
reale, stabile Pfade, unabhängig von venv-Interna. Der Cache ist pro Version
content-adressiert; `catraz` re-extrahiert nur, wenn die Version wechselt.

> **Warum kein reines „curl | sh"?** Der Stack braucht ohnehin Docker; eine Python-
> Installation über `uv`/`pipx` ist der kleinste ehrliche Schritt und hält das
> P7-Versprechen „kein schwergewichtiger Install" (04-cli §3) im Geist: ein Befehl.

### 3.2 Programm vs. Laufzeit — die Grenze, die alles ordnet

| | **Programm** (das installierte Paket) | **Laufzeit** (`.catraz/` im Zielordner) |
| - | ------------------------------------- | --------------------------------------- |
| Versioniert mit | dem Repo / der Tool-Version | dem konkreten Einsatz |
| Wird … | *installiert*, nie editiert | pro Ordner *angelegt & editiert* |
| Enthält | CLI, Dockerfiles, `entrypoint.py`, `AGENT.md`, Warden-/Proxy-Quellen, Compose, Default-Configs | `.env`, `config/`, `claude/`, `state/`, `logs/` |

Damit lösen sich TODO 3 und 4 **als Nebenwirkung** der Distribution: `entrypoint.py`
und `AGENT.md` sind keine Root-Dateien mehr, sondern **Paket-Assets**, die ins Image
gebacken werden. Der Einsteiger im Zielordner sieht sie nie.

### 3.3 Neues Repo-Layout

```
claudecatraz/                       (Programmquelle — installierbar)
├── pyproject.toml                  # Entry-Point + Asset-Packaging
├── src/catraz/
│   ├── cli.py                      # das heutige ./catraz, modularisiert
│   ├── paths.py                    # .catraz-Auflösung, Asset-Lookup (importlib.resources)
│   ├── compose.py                  # Compose-Aufruf (--project-directory/--env-file)
│   ├── auth.py                     # auth_mode-Logik (§6)
│   ├── image.py                    # BASE_IMAGE / Claude-Layer-Build (§5)
│   ├── container/
│   │   └── entrypoint.py           # TODO 3 — war Repo-Root, jetzt Asset
│   └── assets/
│       ├── AGENT.md                # TODO 4 — war Repo-Root, jetzt Asset
│       ├── compose/docker-compose.yml
│       ├── claude-layer/Dockerfile # FROM ${BASE_IMAGE} + Claude/dev-user/entrypoint
│       ├── bases/                  # mitgelieferte Default-Bases (§5.3)
│       │   └── cpp-rust-python/Dockerfile   # das heutige Dockerfile, „degradiert"
│       └── config/                 # Vorlagen, kopiert nach .catraz/config beim init
│           ├── warden.toml  ·  allowlist.txt  ·  squid.conf
├── warden/                         # unverändert (Image-Build-Kontext)
├── forward-proxy/                  # unverändert
└── docs/design/…
```

Assets werden zur Laufzeit über `importlib.resources` aufgelöst, nicht über relative
Pfade — so funktioniert der Lookup auch aus einem installierten Wheel heraus. Docker-
Build-Kontexte zeigen auf die ausgepackten Asset-Verzeichnisse (reale Pfade im venv,
`docker build` kann damit umgehen).

---

## 4. `.catraz/` — das Laufzeit-Heim und der Schutz davor (TODO 6, 7)

### 4.1 On-Disk-Layout im Zielordner

```
<zielordner>/.catraz/
├── .env                     # Secrets + aufgelöste Knöpfe (gitignored, 0600)
├── compose.override.yml     # optional, host-editierbar (§4.4)
├── config/                  # editierbare Kopien der Vorlagen (read-only gemountet)
│   ├── warden.toml  ·  allowlist.txt  ·  squid.conf
├── claude/                  # Claude-Home (Sandbox-Credential ODER leer bei api_key)
├── state/warden/            # SQLite-Quoten-State
└── logs/{warden,squid}/     # Audit-Logs
```

Das ist 1:1 das alte „On-Disk-Layout" aus README §5 — nur **eine Ebene tiefer**, unter
`.catraz/` statt im Repo-Root. Der entscheidende Unterschied: Es liegt jetzt **im
Projektordner des Nutzers**, nicht im geklonten catraz-Repo.

### 4.2 Wie catraz `.catraz/` findet (git-Mental-Model)

`catraz` sucht — wie `git` nach `.git` — vom CWD aufwärts nach `.catraz/`. `init` legt es
im CWD an. Alle Befehle operieren relativ dazu. `find_root()` im heutigen CLI (sucht
`docker-compose.yml` aufwärts) wird zu „suche `.catraz/` aufwärts"; die Compose-Datei
kommt nicht mehr aus dem Ordner, sondern aus dem **Paket-Asset**.

**Geschachtelte `.catraz` sind verboten (Roast-1 #11).** Der Aufwärts-Lauf könnte sonst
einen *größeren* Ahnen-Ordner als `/workspace` binden und damit Geschwister-Projekte
(inkl. deren *nicht* überdecktem `.catraz`) exponieren. `catraz` bricht darum **fail-closed
ab**, wenn unter dem gefundenen Wurzel-Ordner ein *weiteres* `.catraz` liegt, oder wenn
CWD und ein Ahne gleichzeitig eines haben. Der Mount-Wurzel ist immer genau der Ordner,
der das aufgelöste `.catraz` *direkt* enthält — und nur dessen Top-Level-`.catraz` wird
überdeckt, weil kein zweites existieren darf.

### 4.3 Der Kern: Was der Agent von `.catraz/` sehen kann — und was nicht (TODO 7)

**Die Sorge:** Wenn man den Projektordner nach `/workspace` bind-mountet und `.catraz/`
liegt darin, dann liegen Claude-Credentials, Warden-State und Secrets im Lesebereich des
(als kompromittiert angenommenen) Agenten. Das verletzt das Threat-Model (R6).

#### Reichweite der Aussage — ehrlich abgegrenzt (Roast-1 #1)

Der Shadow-Mount schützt **nur den Agent-Container**. Er ist *keine* globale Eigenschaft
„`.catraz` ist für niemanden lesbar". Warden und Proxy mounten **absichtlich** ihre
Scheiben von Host-`.catraz` — sie sind die Vertrauensgrenze und halten die GitLab-Tokens
ohnehin:

| Container | mountet aus `.catraz/` | Modus | warum unbedenklich |
| --------- | ---------------------- | ----- | ------------------ |
| **Agent** | `claude/` → `/home/dev/.claude` | RW (s. u.) | als kompromittiert angenommen → max. eingeschränkt |
| **Warden** | `config/warden.toml` · `state/warden` · `logs/warden` | RO · RW · RW | Trust-Boundary, hält Tokens ohnehin |
| **Proxy** | `config/squid.conf` · `config/allowlist.txt` · `logs/squid` | RO · RO · RW | hält keine Credentials |

Die Invariante, die `doctor` erzwingt (§4.4): **kein** `.catraz`-Pfad, den der Warden/Proxy
sieht, ist *zugleich* vom Agenten erreichbar. Agent-Netz (`agent-net internal`) trennt sie
zusätzlich auf Netzebene.

#### Mechanismus — Shadow-Mount

Docker mountet in Pfad-Reihenfolge: ein Mount auf einen *Unterpfad* überdeckt, was der
äußere Mount dort zeigt. Man bindet den Projektordner nach `/workspace` und **überdeckt
`/workspace/.catraz` mit einem leeren tmpfs**. Wegen der in Roast-1 #4 genannten
Ordering-Quirks der Kurzform wird die **Langform** mit `type: tmpfs` festgenagelt, die
die Mount-Reihenfolge deterministisch nach Ziel-Pfadtiefe auflöst:

```yaml
# docker-compose.yml (claude-dev-env, Auszug) — Langform, NICHT die tmpfs:-Kurzform
services:
  claude-dev-env:
    volumes:
      - type: bind                      # der ganze Ordner …
        source: ${PROJECT_DIR}
        target: /workspace
      - type: tmpfs                     # … aber .catraz wird überdeckt
        target: /workspace/.catraz
        tmpfs: { size: 1048576, mode: 0700 }   # 1 MiB, soll leer bleiben
```

`catraz up` stellt vor dem Start sicher, dass `${PROJECT_DIR}/.catraz` host-seitig
existiert (es legt es ja in `init` an) — damit gibt es den in Roast-1 #4 genannten
„Mountpoint fehlt"-Fall nicht.

#### Das Claude-Home ist die **eine** schreibbare Ausnahme — und sie wird gehärtet (Roast-1 #2, #5)

Das Claude-Home kommt über einen **separaten, gezielten** Mount herein, nicht über
`/workspace`. Es ist die einzige Stelle, an der der Agent host-persistent schreiben kann —
das muss er auch (entrypoint legt `settings.json`, `CLAUDE.md`, `.claude.json`,
`rc-debug.log` an). Die frühere Behauptung „der Agent schreibt **nie** ins Host-`.catraz`"
war damit **falsch**; korrekt ist:

```yaml
    volumes:
      # Credential read-only — der Agent kann sie LESEN (braucht er), aber nicht
      # überschreiben/vergiften (Roast-1 #2):
      - type: bind
        source: ${PROJECT_DIR}/.catraz/claude/.credentials.json
        target: /home/dev/.claude/.credentials.json
        read_only: true
      # Beschreibbarer Rest des Homes — aber NICHT host-persistent: tmpfs, vom
      # entrypoint bei JEDEM Start aus image-gebackenen Quellen neu befüllt.
      - type: tmpfs
        target: /home/dev/.claude
```

Folge: Was der Agent ins Home schreibt (inkl. eines bösartig manipulierten
`settings.json` mit Hooks), lebt im **flüchtigen** tmpfs und ist beim nächsten Start weg.
Der entrypoint **überschreibt** die sicherheitsrelevanten Dateien (`CLAUDE.md`,
`settings.json`) ohnehin bei jedem Start (heute nur „falls fehlend" — wird auf „immer"
geändert). Damit gibt es **keinen Persistenz-Pfad** mehr, über den ein kompromittierter
Lauf den nächsten vergiftet.

> **Subscription-Modus vs. tmpfs-Home:** Die Credential wird RO einzeln gemountet (oben),
> der entrypoint kopiert `.claude.json`/Onboarding-State beim Start aus einer RO-Quelle ins
> tmpfs-Home. Im `api_key`-Modus entfällt der Credential-Mount ganz (§6).

#### Symlinks lösen sich im Container-Namespace auf — kein Host-Escape (Roast-1 #5, Teil-Rebuttal)

Ein in `/workspace` oder im Claude-Home liegender Symlink `evil -> /` oder `-> ../../`
zeigt im Container auf **Container**-`/` bzw. **Container**-`/home` — die Auflösung
geschieht im Mount-Namespace des Containers, nicht auf dem Host. Ein Symlink *kann* dem
Agenten also keinen Host-Pfad erschließen, den er nicht ohnehin gemountet hat. Der einzig
reale Effekt — ein Symlink aus `/workspace` auf `/home/dev/.claude/.credentials.json` —
gibt ihm Lesezugriff auf *seine eigene*, ohnehin in-process gehaltene Credential. Das
red-team-Spec (§4.5) schreibt genau diese Fälle als Negativtests fest.

#### Threat-Model-Tabelle (korrigiert)

| Angriff | Ergebnis |
| ------- | -------- |
| `cat /workspace/.catraz/.env` | tmpfs ist leer — nichts zu lesen. ✔ blockiert |
| `umount /workspace/.catraz` | non-root `dev`, Container nicht privilegiert, kein `CAP_SYS_ADMIN` → `EPERM`. ✔ blockiert |
| Host-`.catraz/claude` über `/home/dev/.claude` **überschreiben** | Credential ist RO; Rest ist tmpfs (flüchtig). ✔ keine Host-Persistenz |
| `/proc/self/mountinfo` lesen | **Topologie** (Mount-Quellpfade) ist sichtbar, **Inhalt** nicht (Roast-1 #3). Akzeptiert: der Agent kann auf einen Pfad, den er nicht erreicht, nicht handeln; Exfil-Kanal hat er über Claude ohnehin. Im red-team-Spec als „darf keinen *erreichbaren* Pfad zeigen" festgeschrieben. |
| Symlink aus `/workspace` / Home heraus | löst im Container-Namespace auf — kein Host-Escape (s. o.). ✔ |

> **Geprüfte Alternativen zum Shadow-Mount:**
>
> | Ansatz | Bewertung |
> | ------ | --------- |
> | **A. tmpfs-Overlay auf `/workspace/.catraz`** | ✅ **Gewählt.** Ein Ordner, git-lokal; der Agent sieht den ganzen Projektbaum *außer* `.catraz`. Kein Kopieren, Host-Edits sofort sichtbar. |
> | **B. `.catraz` als Geschwister außerhalb** (`../proj.catraz/`) | ❌ Bricht das git-Mental-Model („ein Ordner, lokal"); verwaist leicht; schwer auffindbar. |
> | **C. Working-Clone in den Container kopieren** (kein Bind) | ❌ Verliert den Live-Bind (Host/VSCode + Agent teilen denselben Tree — das ist Kern-Feature laut AGENT.md). Sync-Aufwand, divergierende SHAs. |
> | **D. Bind-Mount mit Unterordner-Exclude** | ❌ Docker kann einen Unterpfad eines Bind-Mounts nicht „aussparen"; das tmpfs-Overlay *ist* die idiomatische Umsetzung genau dieses Wunsches. |

### 4.4 Compose-Aufruf aus dem Paket heraus

Die Compose-Datei ist ein **Paket-Asset**, kein Ordner-Artefakt. catraz ruft sie mit
explizitem Projektkontext auf:

```bash
docker compose \
  -f  <paket>/assets/compose/docker-compose.yml \
  --project-directory  <zielordner> \
  --env-file           <zielordner>/.catraz/.env \
  -f  <zielordner>/.catraz/compose.override.yml   # nur wenn vorhanden
  up -d
```

`--project-directory` setzt den Bezugspunkt für relative Bind-Mounts und den
Compose-Projektnamen (so laufen mehrere `.catraz`-Sandboxes nebeneinander, ohne sich
Container-Namen zu klauen — Projektname = Ordnername). `--env-file` zieht Secrets aus
`.catraz/.env`. Die `build.context`-Pfade zeigen auf den **Asset-Cache**
(`~/.cache/catraz/<version>/…`, §3.1), nicht ins venv und nicht auf `--project-directory`.

Die heutigen festen `container_name:` (`claude-dev-env` etc.) müssen **weg** oder
projektpräfix-fähig werden, damit zwei Sandboxes parallel laufen können. Stattdessen
Compose-Default-Namen (`<projekt>-<service>-1`); die semantischen Aliasse aus dem CLI
(`agent`/`warden`/`proxy`) bleiben die Nutzer-Schnittstelle.

#### `compose.override.yml` ist erlaubt, aber `doctor` prüft die Grenze *nach* dem Merge (Roast-1 #12)

Eine optionale `.catraz/compose.override.yml` erlaubt fortgeschrittene Anpassungen, ohne
das Paket-Asset zu berühren. Sie ist host-seitig (nicht agent-erreichbar) — aber ein
Override darf per Docker-Semantik die Vertrauensgrenze *auflösen* (Egress zu `agent-net`,
Token-Env, `privileged: true`, den Shadow-Mount entfernen). Darum prüft `doctor` nicht das
Asset, sondern die **aufgelöste** Konfiguration (`docker compose config`) gegen harte
Invarianten — und `up` bricht ab, wenn eine verletzt ist:

| Invariante (nach Override-Merge geprüft) | warum |
| ---------------------------------------- | ----- |
| `agent-net.internal == true` | kein Eigen-Egress des Agenten (R6) |
| Agent-Service ohne `GITLAB_*_TOKEN`-Env | kein Token im Agenten (R6) |
| Agent-Service nicht `privileged`, kein `CAP_SYS_ADMIN` | sonst fällt der Shadow-Mount (§4.3) |
| tmpfs-Shadow auf `/workspace/.catraz` vorhanden | TODO 7 |
| Agent mountet keinen Warden/Proxy-`.catraz`-Pfad | §4.3 Reichweite |

Das macht die Grenze **prüfbar statt angenommen** — und ist zugleich die Antwort auf den
Transparenz-Verlust (§7): die Invarianten sind explizit und maschinell verifiziert.

### 4.5 Verifikations-Spec: der Shadow-Mount ist *definiert* durch seine Negativtests (Roast-1, Kernforderung)

Die §4.3-Garantie ist zu tragend, um sie zu *behaupten*. Sie wird als ausführbare
red-team-Suite (`tests/redteam/test_shadow_mount.py`) geschrieben — **bevor** der
Mount-Code steht — und gilt nur als „funktionierend", wenn alle Fälle grün sind, über
**jede** unterstützte Docker-/Compose-Version (CI-Matrix):

| # | Negativtest (im Agent-Container ausgeführt) | erwartetes Ergebnis |
| - | -------------------------------------------- | ------------------- |
| T1 | `ls -A /workspace/.catraz` | leer |
| T2 | Mount-Typ von `/workspace/.catraz` (`findmnt -no FSTYPE`) | `tmpfs`, nicht der Bind |
| T3 | Agent schreibt `/workspace/.catraz/x`; danach Host-`.catraz` prüfen | Host unverändert, kein `x` |
| T4 | `umount /workspace/.catraz` als `dev` | scheitert (`EPERM`) |
| T5 | `cat /home/dev/.claude/.credentials.json > …` (überschreiben) | scheitert (RO) |
| T6 | Agent schreibt `~/.claude/settings.json`; Container neu starten; Datei prüfen | vom entrypoint überschrieben, keine Agent-Hooks überleben |
| T7 | Symlink `/workspace/link -> /` bzw. `-> /home/dev/.claude`; auflösen | bleibt im Container-Namespace, kein Host-Pfad |
| T8 | `grep` in `/proc/self/mountinfo` nach einem *erreichbaren* Host-Secret-Pfad | keiner erreichbar (Topologie sichtbar, Inhalt/Reichweite nicht) |
| T9 | nach `down`+`up --build` während Warden-Schreibvorgang: Quota-DB lesbar | konsistent oder fail-closed, nie fail-open (§7 Recreate) |

Diese Tabelle *ist* die Spezifikation. Findet ein Test eine Lücke, ist der Shadow-Mount
nicht fertig — nicht das Dokument wird angepasst, sondern der Mount.

### 4.6 `.gitignore`-Hygiene

`init` trägt `.catraz/` in die `.gitignore` des Zielordners ein (oder, wenn keine da ist
bzw. nicht gewünscht, in `.git/info/exclude`). So nistet sich catraz ein, ohne das
Projekt-Repo zu verschmutzen — wie ein gut erzogenes Tool.

---

## 5. Image-Schichtung — beliebige Bases, garantierter Claude-Layer (TODO 2)

### 5.1 Das Problem mit dem heutigen Dockerfile

Das heutige `Dockerfile` ist **monolithisch**: Es vermischt zwei Anliegen, die
verschiedene Eigentümer haben sollten.

| Schicht | Was sie tut | Wem sie „gehört" |
| ------- | ----------- | ---------------- |
| **Toolchain** | Ubuntu + C++/Rust/Python/Conan/Node | dem **Nutzer** (sein Projekt, seine Sprachen) |
| **Claude-Layer** | Claude Code, `dev`-User (non-root), `gosu`, `entrypoint.py`, `AGENT.md`, Netz-/Proxy-Wiring | **catraz** (Sicherheit + Komfort, muss „richtig" sein) |

Wer Go, Java oder eine ganz andere Toolchain will, muss heute das catraz-Dockerfile
gabeln und dabei riskieren, die sicherheitsrelevante untere Schicht falsch zu treffen
(als root laufen, `entrypoint` vergessen, Proxy-ENV verlieren).

### 5.2 Die Lösung: zwei Dockerfiles, `FROM ${BASE_IMAGE}`

catraz besitzt nur noch den **Claude-Layer** als dünnes Overlay-Dockerfile:

```dockerfile
# src/catraz/assets/claude-layer/Dockerfile  (catraz-eigen, garantiert „richtig")
# Der Claude-Layer sitzt OBEN (FROM base): die letzte Schicht gewinnt, und catraz
# besitzt sie — USER, ENTRYPOINT, dev-User sind damit secure-by-construction, egal
# was die Base tat. Das ist der Sicherheitsvorteil gegenüber „Base FROMt claude" (§5.5).
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ARG NODE_VERSION=22
ARG CLAUDE_CODE_VERSION=latest
ARG DEV_UID=1000

# Node + Claude Code. Annahme: Debian/Ubuntu-Familie (apt) mit python3 — siehe §5.4.
# nodesource wie heute; bewusst KEIN „funktioniert auch auf Alpine/musl"-Versprechen.
RUN curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash - && \
    apt-get install -y nodejs && rm -rf /var/lib/apt/lists/* && \
    npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}

# gosu als statisches Binary mitgeliefert (kein Paketmanager-Zwang, base-stabil).
COPY --chmod=0755 gosu /usr/local/bin/gosu

# non-root dev-user. Den UID-1000-Konflikt (Ubuntus mitgelieferter `ubuntu`-User)
# auflösen — sonst scheitert useradd -u 1000 (Roast-1 #7, heute in Dockerfile:62 gelöst):
RUN (userdel -r ubuntu 2>/dev/null || true) && \
    useradd -m -u ${DEV_UID} -s /bin/bash dev

COPY entrypoint.py            /entrypoint.py
COPY AGENT.md                 /opt/claude-dev-env/AGENT.md
ENV  HOME=/home/dev
USER dev
WORKDIR /workspace
ENTRYPOINT ["python3", "/entrypoint.py"]
```

Der Nutzer liefert die **Base** auf eine von zwei Arten:

| Modus | `.catraz/.env` | Was catraz tut |
| ----- | -------------- | -------------- |
| **Prebuilt** | `BASE_IMAGE=myorg/devenv:1.4` | nutzt das Image direkt als `FROM`-Argument. |
| **Eigenes Dockerfile** | `BASE_DOCKERFILE=./Dockerfile.dev` | baut es zuerst, taggt content-adressiert (s. u.), nutzt das Ergebnis als `BASE_IMAGE`. |

**Cache-/Tag-Schema (Roast-1 #8).** Die Base wird als `catraz-base:<sha256-12>` getaggt,
wobei der Hash über *Dockerfile-Inhalt + relevanten Build-Kontext* gebildet wird —
deterministisch, kein stales-Reuse, keine verwaisten Images bei jeder Edit (alte Tags
räumt `catraz prune` auf). Bei Zwei-Phasen-Fehlern (Base ok, Claude-Layer scheitert)
meldet `catraz` *welche* Phase brach und lässt den gültigen Base-Tag stehen.

`catraz up --build` orchestriert: erst (optional) die Base bauen, dann den Claude-Layer
`FROM` der Base. So bleibt der Komfort **und** die Freiheit erhalten.

### 5.3 Die heutige Toolchain wird zur mitgelieferten Default-Base

Das jetzige Ubuntu+C++/Rust/Python-Dockerfile verschwindet nicht — es wird **degradiert**
zu *einer* mitgelieferten Base unter `src/catraz/assets/bases/cpp-rust-python/`. Wenn der
Nutzer **keine** Base angibt, nimmt catraz diese Default-Base. Aus „dem Image" wird „dem
Default unter mehreren". Konfiguration:

```dotenv
# .catraz/.env
# Leer/unset  → mitgelieferte Default-Base (cpp-rust-python)
# BASE_IMAGE=ghcr.io/acme/rust-only:latest
# BASE_DOCKERFILE=./Dockerfile
```

### 5.4 Base-Anforderung — ehrlich verengt (Roast-1 #7)

Die erste Skizze versprach „läuft auf Alpine/RHEL/musl". Das war Hand-Waving: Nodes
offizielle Binaries sind glibc, ein `install-node.sh`-Asset existierte nicht, und
busybox-`adduser` hat andere Flags. Statt ein leeres Versprechen zu pflegen, **verengen
wir die Anforderung auf das real Belastbare**:

> **Base-Vertrag:** Debian-/Ubuntu-Familie (apt), glibc, `python3` vorhanden.

Das deckt praktisch jedes reale Dev-Image (die ganz überwiegende Mehrheit) und hält den
Claude-Layer **einfach** (apt-nodesource wie heute, `userdel ubuntu`-Guard portiert,
statisches `gosu`). Kein erfundenes `install-node.sh`, keine `||`-Fallbacks, die eine
nicht-getestete Plattform vortäuschen.

`doctor base` prüft den Vertrag *laut* nach dem Base-Build, statt ihn still beim Start zu
verlieren: `docker run --rm $BASE sh -c 'command -v apt-get && python3 --version'`. Schlägt
das fehl → ❌ mit klarer Meldung „Base braucht apt + python3".

### 5.5 Geprüfte Alternative: publizierte Base statt Zwei-Phasen-Build (Roast-1 #8)

Der Roast fragt zu Recht, ob die Zwei-Phasen-Orchestrierung ihren Preis wert ist, wo eine
publizierte `claudecatraz/claude-base`, die der Nutzer selbst `FROM`t, mit *einem* Build
auskäme. Die Abwägung:

| Ansatz | Builds | „richtig garantiert"? | Bewertung |
| ------ | ------ | --------------------- | --------- |
| **A. Claude-Layer OBEN, `FROM ${BASE}`** (gewählt) | zwei | **ja** — catraz besitzt die *letzte* Schicht (`USER dev`, `ENTRYPOINT`); die Base kann sie nicht versehentlich aufheben | mehr Build-Maschinerie, aber Sicherheit secure-by-construction |
| **B. publizierte `claude-base`, Nutzer `FROM`t sie** | einer | **nein** — die Nutzer-Schicht ist *letzte*; ein vergessenes `USER root` am Ende hebt die Härtung auf | einfacher, aber „done right" liegt beim Nutzer |

Weil **Sicherheit vor Einfachheit** rangiert (und TODO 2 ausdrücklich „die Sicherheit, dass
das richtig gemacht wird" verlangt), bleibt **A primär**. B wird als dokumentierter,
gleichwertig *einfacher* Pfad für Nutzer angeboten, die bewusst Ein-Phasen wollen und die
Verantwortung für die Reihenfolge übernehmen — aber `doctor` prüft auch dort die
aufgelösten Compose-Invarianten (§4.4), sodass ein gebrochenes B nicht still hochfährt.
Damit steht A nicht *allein*, ist aber auf der Kern-Achse (Sicherheit) klar besser — genau
die Bedingung, unter der ein zweiter Pfad koexistieren darf.

---

## 6. Auth-Modus — subscription ⊻ api_key (TODO 1)

### 6.1 Das heutige Durcheinander

Heute kann **beides gleichzeitig** halb-aktiv sein: `entrypoint.py cmd_start` *verlangt*
`.credentials.json` (Subscription-Pfad), während compose *zusätzlich* `ANTHROPIC_API_KEY`
in den Container reicht. Welcher Pfad „gewinnt", ist unklar; gibt man einen API-Key an
und hat keine Credentials, scheitert der Start trotzdem an der Credential-Pflicht.

### 6.2 Die Lösung: ein expliziter Modus, genau einer aktiv

```dotenv
# .catraz/.env
AUTH_MODE=subscription        # subscription | api_key

# subscription: Credentials werden vom Host-Claude-Konto importiert.
#   Quelle ist konfigurierbar (TODO 1: „claude home im .env angeben, von wo kopiert wird"):
CLAUDE_CREDENTIAL_SOURCE=~/.claude     # default; auf abweichendes Host-Konto zeigbar

# api_key: dedizierter Sandbox-Key, KEINE Credential-Pflicht.
# ANTHROPIC_API_KEY=sk-…
```

**Regeln (von `doctor` erzwungen, fail-closed):**

| `AUTH_MODE` | muss gesetzt sein | darf **nicht** | `up` tut automatisch |
| ----------- | ----------------- | -------------- | -------------------- |
| `subscription` | gültiges `.catraz/claude/.credentials.json` | `ANTHROPIC_API_KEY` wird **nicht** in den Container gereicht | falls Credential fehlt/veraltet → **`sync` automatisch** aus `CLAUDE_CREDENTIAL_SOURCE` |
| `api_key` | `ANTHROPIC_API_KEY` (nicht-leer) | `.catraz/claude/` enthält **keine** Credentials (sonst Ambiguität) | Key wird in den Container gereicht; Credential-Pflicht im entrypoint entfällt |

`doctor` meldet ❌, wenn **beide** oder **keiner** der Pfade erfüllt sind — das ist die
vom TODO geforderte gegenseitige Ausschließung, technisch erzwungen statt nur dokumentiert.
**Konkreter Enforcement-Punkt (Roast-1 #14):** `doctor auth` scheitert, wenn
`AUTH_MODE=api_key` **und** `.catraz/claude/.credentials.json` existiert (sonst entstünde
genau die Ambiguität, die der Modus beseitigen soll), und ebenso, wenn
`AUTH_MODE=subscription` und `ANTHROPIC_API_KEY` gesetzt ist.

### 6.3 Konkrete Code-Änderungen

- **`entrypoint.py`**: Credential-Pflicht (`cmd_start`) wird modusabhängig. Bei
  `AUTH_MODE=api_key` keine `.credentials.json`-Prüfung; bei `subscription` kein
  Verlass auf `ANTHROPIC_API_KEY`.
- **compose**: `ANTHROPIC_API_KEY` nur im `api_key`-Modus durchreichen (z. B. über ein
  Compose-Profil oder eine von catraz gesetzte leere/gefüllte Variable). Der Mount von
  `.catraz/claude` bleibt in beiden Modi (im api_key-Modus leer — für `settings.json`,
  `.claude.json`-Onboarding-State etc.).
- **`sync`**: liest die Quelle aus `CLAUDE_CREDENTIAL_SOURCE` statt fest aus `~/.claude`
  (das `entrypoint.py cmd_sync` heute hart kodiert), und `up`/`init` rufen ihn im
  Subscription-Modus automatisch, wenn die Credential fehlt.

---

## 7. Auswirkungen auf die CLI

Die Befehlsoberfläche (04-cli §4) bleibt — `init`/`doctor`/`up`/`down`/`status`/`logs`/
`sync`. Geändert wird das **Innenleben**:

| Bereich | Vorher | Nachher |
| ------- | ------ | ------- |
| Projektwurzel | `find_root` sucht `docker-compose.yml` aufwärts | sucht **`.catraz/`** aufwärts; Compose kommt aus dem Paket-Asset |
| `init` | legt Dirs im Repo-Root an | legt **`.catraz/`** an, kopiert Config-Vorlagen hinein, schreibt `.gitignore`-Eintrag, fragt zusätzlich `AUTH_MODE` |
| Secrets/State-Pfade | `./claude`, `./state`, `./logs`, `.env` | `.catraz/claude`, `.catraz/state`, `.catraz/logs`, `.catraz/.env` |
| `doctor` | Checks `docker/compose/env/tokens/policy/claude/net` | **+ `auth`** (Modus-XOR, §6.2), **+ `base`** (Base-Image baubar/python3 vorhanden, §5.4); `claude`-Check wird modusabhängig |
| `up` | `docker compose up -d` im Ordner | Paket-Compose mit `--project-directory`/`--env-file`; optionaler Base-Build vor Claude-Layer-Build |
| Compose-Invocation | implizit (cwd) | explizit `-f <asset> --project-directory <ziel> --env-file <ziel>/.catraz/.env` |

`--print` (P4) zeigt weiterhin das exakte Compose-Kommando — jetzt inklusive der
Paket-Pfade und `--project-directory`, damit die Vertrauensgrenze sichtbar bleibt.

### 7.1 Transparenz trotz versteckter Assets (Roast-1 #10)

`--print` zeigt die *Invocation*, nicht den *Inhalt* der nun im Cache liegenden
Compose-/Dockerfiles. Für ein Werkzeug, dessen Seele die *nachvollziehbare* Isolation ist
(04-cli §7), ist das zu wenig. Zwei Ergänzungen:

- **`catraz show <compose|claude-layer|dockerfile|warden>`** druckt den **tatsächlichen
  Asset-Inhalt** (aus dem Cache), damit man „ist `agent-net` wirklich `internal`, hält der
  Agent wirklich kein Token" ohne venv-Spelunking prüfen kann.
- `init` legt eine **read-only Referenzkopie** der aufgelösten Compose-Konfiguration
  (`docker compose config`) als `.catraz/compose.resolved.yml` ab — der eine Ort, an dem
  die *effektive* Topologie (nach Override-Merge) inspizierbar ist.

### 7.2 Recreate-/Update-Semantik gegen den laufenden Stack (Roast-1 #6)

`up --build` / „update" rekreiert den Stack, während der Warden SQLite-WAL + Audit-JSONL in
`.catraz/state`/`logs` schreibt. Festlegungen:

- **Graceful**: Recreate sendet `SIGTERM` (kein `kill -9`); SQLite-WAL ist crash-konsistent
  und checkpointet beim sauberen Stop — die Quota-DB überlebt.
- **Fail-closed, nie fail-open**: Der Warden muss bei *unlesbarem* State-DB **schließen**
  (keine Schreibrechte gewähren), nicht öffnen — R5 ist eine Sicherheitskontrolle. Das ist
  eine Warden-Invariante, die der red-team-Test T9 (§4.5) festschreibt.
- `up` rekreiert nicht **stillschweigend** über einen laufenden, gesunden Stack hinweg:
  ohne `--build`/`--recreate` ist `up` idempotent (no-op bei gesundem Stack); mit `--build`
  warnt es, dass rekreiert wird.

---

## 8. Migration von der alten Struktur

Bestehende Installationen liegen flach im Repo-Root. Sanfter Pfad:

1. **`catraz migrate`** (einmaliger Helfer): legt `.catraz/` an und verschiebt
   `config/ state/ logs/ claude/ .env` hinein; trägt `.catraz/` in `.gitignore` ein;
   warnt, wenn `entrypoint.py`/`AGENT.md`/`Dockerfile` noch im Root liegen (jetzt
   Paket-Assets — die Root-Kopien können weg, sobald `uv tool install` lief).
2. **Fail-closed (Roast-1 #13):** Eine halb fertige Migration darf nicht starten. Bleibt
   nach `migrate` *irgendein* Alt-Layout-Secret-Verzeichnis (`./claude`, `./state`,
   `./.env`) unter dem Projekt-Root liegen, **verweigert `up`** — sonst läge ein
   *nicht* überdecktes `./claude` im nach `/workspace` gemounteten Baum. `migrate`
   verschiebt atomar (rename, kein copy+delete) und prüft am Ende auf Reste.
3. **Präzedenz explizit:** Existieren `.catraz/` *und* Alt-Layout gleichzeitig, **gewinnt
   `.catraz/`** und `up` verweigert mit Hinweis, das Alt-Layout zu entfernen — kein stilles
   Vermischen.
4. Das Repo selbst behält für eine Version die Root-`./catraz`-Shim (`exec python -m
   catraz.cli "$@"`), damit Klone ohne Install nicht hart brechen.

---

## 9. Risiken & offene Fragen

| Thema | Risiko / Frage | Entschärfung |
| ----- | -------------- | ------------ |
| **Shadow-Mount-Robustheit** | Garantie ruht auf non-privileged + non-root + Mount-Ordering. | Als **Spec** festgeschrieben (§4.5 T1–T9), nicht behauptet; Langform-tmpfs (§4.3); CI-Matrix über Docker-Versionen. |
| **Claude-Home-Persistenz** | Agent könnte Host-Credential/Hooks vergiften. | Credential RO einzeln gemountet, Rest tmpfs (flüchtig), entrypoint überschreibt Sicherheits-Dateien je Start (§4.3, T5/T6). |
| **mountinfo-Topologie** | Host-Pfade in `/proc/self/mountinfo` sichtbar. | Akzeptiert: Topologie ≠ Reichweite; T8 verlangt „kein *erreichbarer* Secret-Pfad". |
| **Override löst Grenze auf** | `compose.override.yml` könnte R6 brechen. | `doctor` prüft *aufgelöste* Compose-Invarianten nach Merge (§4.4). |
| **Asset-Auflösung im Wheel** | `docker build` braucht reale Pfade; zip-Installs haben keine. | Deterministische Extraktion nach `~/.cache/catraz/<version>/`; `warden/`+`forward-proxy/` als Wheel-Includes (§3.1). |
| **Image-Schichtung-Komplexität** | Zwei-Phasen-Build, Cache, Fehlerpfade. | content-adressierter `catraz-base:<hash>`-Tag, Phasen-getrennte Fehlermeldung (§5.2); Ein-Phasen-Alternative B dokumentiert (§5.5). |
| **Base-Vertrag** | Fremde Base ohne apt/python3. | Ehrlich verengt auf Debian/Ubuntu+glibc+python3; `doctor base` prüft laut (§5.4). |
| **Geschachtelte `.catraz`** | Falscher, größerer Mount-Root exponiert Geschwister. | `find_root` bricht fail-closed bei verschachteltem `.catraz` ab (§4.2). |
| **Recreate vs. Warden-Schreiben** | WAL-Korruption, fail-open der Quota. | Graceful `SIGTERM`, WAL crash-konsistent, Warden fail-closed bei unlesbarem State (§7.2, T9). |
| **Migration halb fertig** | Alt-`./claude` unüberdeckt im Mount. | `up` verweigert bei Alt-Layout-Resten; atomic rename; Präzedenz `.catraz` (§8). |
| **Transparenz versteckter Assets** | Compose/Dockerfile nicht mehr im Klon sichtbar. | `catraz show <asset>` + read-only `.catraz/compose.resolved.yml` (§7.1). |
| **`CLAUDE_CREDENTIAL_SOURCE` mit `~`** | Tilde-Expansion außerhalb der Shell. | catraz expandiert `~`/Env selbst (wie heute `_claude_home`). |

### Offen / bewusst dem Implementierungs-Spike überlassen

- **Mount-Ordering über *alle* Compose-Versionen:** §4.5 T2 ist der Gate — falls eine
  Ziel-Version die Langform nicht deterministisch ordnet, fällt die Entscheidung auf einen
  expliziten Pre-Start-Mount (catraz mountet das tmpfs per `docker run`-Flags statt Compose).
- **`docker compose config`-Invariantenparser (§4.4):** Umfang der geprüften Felder final
  beim Bau festzurren; Risiko, dass Compose-Schema-Änderungen den Parser brechen → gegen
  das *aufgelöste* JSON prüfen, nicht gegen YAML-Text.

---

## 10. Umsetzungsreihenfolge (inkrementell, kein Big-Bang)

Jeder Schritt ist für sich nützlich und unabhängig testbar.

| Schritt | Liefert | TODO |
| ------- | ------- | ---- |
| **1. Paketierung** | `pyproject.toml`, `src/catraz/`-Layout, `entrypoint.py`+`AGENT.md` als Assets, Root-Shim. `uv tool install` funktioniert, Verhalten unverändert. | 3, 4, 5 |
| **2. `.catraz/`-Heim** | `init`/`doctor`/Pfade auf `.catraz/` umstellen; `--project-directory`/`--env-file`; `.gitignore`-Eintrag; `migrate`. | 6 |
| **3. Shadow-Mount** | tmpfs-Overlay auf `/workspace/.catraz`, gezielter Claude-Home-Mount; Red-Team-Negativtest. | 7 |
| **4. Auth-Modus** | `AUTH_MODE`-XOR in `doctor`/`entrypoint`/compose; `CLAUDE_CREDENTIAL_SOURCE`; Auto-`sync`. | 1 |
| **5. Image-Schichtung** | Claude-Layer-Dockerfile `FROM ${BASE_IMAGE}`; Default-Base unter `assets/bases/`; `BASE_IMAGE`/`BASE_DOCKERFILE`-Modi; `doctor base`. | 2 |

Reihenfolge-Logik: Erst die **Hülle** (Paket + `.catraz/`), weil sie die Pfade definiert,
auf denen alles andere aufsitzt. Dann der **Sicherheits-Kern** (Shadow-Mount), weil er den
im TODO benannten Hauptzweifel auflöst. Dann **Auth** und **Image-Schichtung** als
unabhängige Komfort-/Flexibilitäts-Schichten obendrauf.
