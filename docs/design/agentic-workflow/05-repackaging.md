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
# Assets MÜSSEN ins Wheel — sonst fehlt der Build-Kontext nach der Installation:
[tool.hatch.build.targets.wheel.force-include]
"src/catraz/assets"   = "catraz/assets"
"src/catraz/container"= "catraz/container"
```

```bash
uv tool install ./claudecatraz      # oder: pipx install ./claudecatraz
catraz --version
```

`catraz` lebt danach in einem isolierten venv und liegt als Shim auf dem PATH.
Das geklonte Repo wird **nicht mehr gebraucht** (außer für Updates: `uv tool upgrade`).

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

### 4.3 Der Kern: Warum der Agent `.catraz/` nicht lesen kann (TODO 7)

**Die Sorge:** Wenn man den Projektordner nach `/workspace` bind-mountet und `.catraz/`
liegt darin, dann liegen Claude-Credentials, Warden-State und Secrets im Lesebereich des
(als kompromittiert angenommenen) Agenten. Das verletzt das Threat-Model (R6).

**Die Lösung — Shadow-Mount.** Docker mountet Bind-Mounts in Pfad-Reihenfolge: ein Mount
auf einen *Unterpfad* überdeckt, was der äußere Mount dort zeigt. Man bindet also den
Projektordner nach `/workspace` und **überdeckt `/workspace/.catraz` mit einem leeren
tmpfs**:

```yaml
# docker-compose.yml (claude-dev-env, Auszug)
services:
  claude-dev-env:
    volumes:
      - ${PROJECT_DIR}:/workspace                 # der ganze Ordner …
    tmpfs:
      - /workspace/.catraz:uid=${DEV_UID},mode=0700   # … aber .catraz wird überdeckt
```

Im Container ist `/workspace/.catraz` damit ein **leeres, beschreibbares tmpfs** — der
echte Host-Inhalt ist strukturell unsichtbar. Schreibt der Agent dort hinein, landet es
im flüchtigen tmpfs, **nie** im Host-`.catraz`.

**Warum das hält (Threat-Model-Analyse):**

| Angriff | Warum er scheitert |
| ------- | ------------------ |
| `cat /workspace/.catraz/.env` | tmpfs ist leer — es *gibt* dort nichts zu lesen. |
| `umount /workspace/.catraz`, um den Host-Inhalt freizulegen | Agent läuft als non-root `dev`, Container **nicht** privilegiert, kein `CAP_SYS_ADMIN` → `umount` schlägt mit `EPERM` fehl. Strukturell, nicht per Config. |
| über `/proc/…/root` oder Mount-Namespace ausbrechen | erfordert ebenfalls Privilegien, die der Container nicht hat. |
| symlink/`..`-Traversal aus `/workspace` heraus | bleibt im Bind-Mount; `.catraz` ist die einzige Stelle mit Host-Secrets und die ist überdeckt. |

Die Credentials, die der Agent *legitim* braucht (Claude-Home), kommen über einen
**separaten, gezielten** Mount herein — nicht über `/workspace`:

```yaml
    volumes:
      - ${PROJECT_DIR}:/workspace
      - ${PROJECT_DIR}/.catraz/claude:/home/dev/.claude   # nur das Claude-Home
```

Host-seitig liegt `.catraz/claude` zwar *innerhalb* von `/workspace`s Quelle, aber im
Container erreicht der Agent es nur unter `/home/dev/.claude` — und `/workspace/.catraz`
ist überdeckt. Es gibt keinen Pfad, über den er von `/workspace` aus an das echte
`.catraz` käme.

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
`.catraz/.env`. Eine optionale `compose.override.yml` erlaubt fortgeschrittene Nutzer-
Anpassungen, ohne das Paket-Asset zu berühren (P5: Transparenz, P4: `--print` zeigt das
volle Kommando).

Die heutigen festen `container_name:` (`claude-dev-env` etc.) müssen **weg** oder
projektpräfix-fähig werden, damit zwei Sandboxes parallel laufen können. Stattdessen
Compose-Default-Namen (`<projekt>-<service>-1`); die semantischen Aliasse aus dem CLI
(`agent`/`warden`/`proxy`) bleiben die Nutzer-Schnittstelle.

### 4.5 `.gitignore`-Hygiene

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
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

# --- Claude-Layer: base-agnostisch, was das Sicherheitsmodell braucht ---
ARG NODE_VERSION=22
ARG CLAUDE_CODE_VERSION=latest
ARG DEV_UID=1000

# Node + Claude Code (base-agnostisch via offiziellem Tarball nach /usr/local,
# statt apt — damit auch Nicht-Debian-Bases funktionieren).
RUN install-node.sh "${NODE_VERSION}" && \
    npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}

# gosu als statisches Binary mitgeliefert (kein Paketmanager-Zwang).
COPY --chmod=0755 gosu /usr/local/bin/gosu

# non-root dev-user (Claude Code verweigert bypassPermissions als root).
RUN useradd -m -u ${DEV_UID} -s /bin/bash dev || adduser -D -u ${DEV_UID} dev

COPY entrypoint.py            /entrypoint.py
COPY AGENT.md                 /opt/claude-dev-env/AGENT.md
ENV  HOME=/home/dev
WORKDIR /workspace
ENTRYPOINT ["python3", "/entrypoint.py"]
```

Der Nutzer liefert die **Base** auf eine von zwei Arten:

| Modus | `.catraz/.env` | Was catraz tut |
| ----- | -------------- | -------------- |
| **Prebuilt** | `BASE_IMAGE=myorg/devenv:1.4` | nutzt das Image direkt als `FROM`-Argument. |
| **Eigenes Dockerfile** | `BASE_DOCKERFILE=./Dockerfile.dev` | baut es zuerst (`docker build -t catraz-base:<hash> .`), nutzt das Ergebnis als `BASE_IMAGE`. |

`catraz up --build` orchestriert beide Schritte: erst (optional) die Base bauen, dann den
Claude-Layer `FROM` der Base. So bleibt der Komfort („catraz kümmert sich um den
sicherheitskritischen Teil") **und** die Freiheit (beliebige Toolchain) erhalten.

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

### 5.4 Base-agnostische Zwänge (ehrlich benannt)

Damit der Claude-Layer auf *beliebigen* Bases sitzt, muss er Annahmen vermeiden, die nur
auf Ubuntu gelten:

- **Node nicht via `apt`**, sondern über offiziellen Tarball nach `/usr/local` — sonst
  scheitert eine Alpine-/RHEL-Base. (Helper-Script `install-node.sh` im Layer-Kontext.)
- **`gosu` als statisches Binary mitliefern** statt `apt-get install gosu`.
- **User-Anlage** mit `useradd`-`||`-`adduser`-Fallback (glibc vs. busybox).
- **Dokumentierte Mindestannahme:** glibc- *oder* musl-Linux mit `python3` im Image
  (Claude Code + entrypoint brauchen Python). Das ist die eine Bedingung, die wir an
  fremde Bases stellen — `doctor` prüft sie nach dem Base-Build (`docker run --rm
  $BASE python3 --version`) und meldet sie *laut*, statt sie still beim Start zu
  verlieren.

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

---

## 8. Migration von der alten Struktur

Bestehende Installationen liegen flach im Repo-Root. Sanfter Pfad:

1. **`catraz migrate`** (einmaliger Helfer): legt `.catraz/` an und verschiebt
   `config/ state/ logs/ claude/ .env` hinein; trägt `.catraz/` in `.gitignore` ein;
   warnt, wenn `entrypoint.py`/`AGENT.md`/`Dockerfile` noch im Root liegen (jetzt
   Paket-Assets — die Root-Kopien können weg, sobald `uv tool install` lief).
2. **Übergangsweise** akzeptiert `find_root` weiterhin die alte flache Struktur und
   meldet eine Deprecation-Warnung mit Hinweis auf `catraz migrate`.
3. Das Repo selbst behält für eine Version die Root-`./catraz`-Shim (`exec python -m
   catraz.cli "$@"`), damit Klone ohne Install nicht hart brechen.

---

## 9. Risiken & offene Fragen

| Thema | Risiko / Frage | Entschärfung |
| ----- | -------------- | ------------ |
| **Shadow-Mount-Robustheit** | Hängt davon ab, dass der Container *nie* privilegiert läuft und der Agent *nie* root ist. | Beides ist bereits Invariante (non-root `dev`, kein `privileged`); ein Red-Team-Test (`tests/redteam/`) muss `umount`/Lese-Versuch auf `/workspace/.catraz` als Negativ-Fall festschreiben. |
| **Base-Agnostik** | Fremde Base ohne `python3`/glibc bricht den Claude-Layer. | `doctor base`-Check (§5.4) macht es laut; dokumentierte Mindestannahme. |
| **Asset-Auflösung im Wheel** | `importlib.resources` vs. `docker build`-Kontext (braucht reale Pfade). | Assets als `force-include` ins Wheel; Build-Kontext aus dem entpackten venv-Pfad — `uv tool` entpackt real, kein zip-import. Für `pip install`-in-zip ggf. nach Tempdir extrahieren. |
| **Parallel-Sandboxes** | Feste `container_name:` kollidieren. | Entfernen; Compose-Projektname = Zielordner (§4.4). |
| **`CLAUDE_CREDENTIAL_SOURCE` mit `~`** | Tilde-Expansion außerhalb der Shell. | catraz expandiert `~`/Env selbst (wie heute `_claude_home`). |
| **tmpfs-Größe** | Agent füllt `/workspace/.catraz`-tmpfs. | klein dimensionieren (`size=1m`) — es soll leer bleiben, nicht genutzt werden. |
| **Warden/Proxy-Build-Kontexte** | liegen als Paket-Asset, nicht im Zielordner. | Compose-`build.context` zeigt auf Paket-Pfade (absolut, via catraz aufgelöst), nicht relativ zum `--project-directory`. |

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
