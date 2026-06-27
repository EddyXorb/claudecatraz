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

### 4.0 Topologie-Entscheidung: `.catraz/` im Baum (Roast-2 #2, in Roast-3 entschieden)

Die größte Designfrage: muss `.catraz/` *physisch im bind-gemounteten Baum* liegen? Tut es
das, muss man es dort vor dem Agenten verstecken (Shadow-Mount). Liegt der echte State
*außerhalb*, entfällt das Versteck — aber dann liegen Claude-Ordner und Logs **nicht mehr in
`.catraz`**. Roast-2 stellte zwei Optionen gegenüber; Roast-3 verlangte zu Recht eine
**Entscheidung** statt eines „beide gleichwertig"-Ausweichens. Hier ist sie.

| | **Option I — `.catraz/` im Baum** (GEWÄHLT) | **Option II — Marker im Baum, State außerhalb** |
| - | -------------------------------------------- | ----------------------------------------------- |
| Claude-Ordner & Logs in `.catraz`? | **ja** (= TODO 6/7 wörtlich) | **nein** — in `~/.local/state/catraz/<id>/` |
| `/workspace`-Bind enthält Secrets? | ja → Shadow-Mount nötig | nein → nichts zu verstecken |
| Zusatz-Komplexität | Shadow-Mount + T1–T4/T7/T8 + geschachtelt-Guard | externer State-Lifecycle: `project-id`, `gc`, `relink`-bei-Umzug |
| Verwaisung / Umzug | `rm -rf .catraz` = sauber; Umzug nimmt State mit | verwaister externer State; Umzug bricht die Bindung |

**Warum Option I — und *nicht* das von Roast-3 vorgeschlagene Umkippen auf II:**

1. **Option II verletzt eine *explizite, zweifach genannte* Anforderung.** TODO 6: „darin
   liegen dann **alle** Einstellungen und Hilfsdateien wie der **Claude-Ordner und die
   Logs**." TODO 7: „alles im `.catraz`-Ordner einnistet." II schiebt genau diese Dateien
   *aus* `.catraz` heraus. Das ist keine Priorität-4-Politur, die man wegoptimieren darf —
   es ist die formulierte Funktion. Roast-3s lexikografisches Argument („Sicherheit > … >
   Anwenderfreundlichkeit, also II") fußt auf einer **strikten** Wertordnung, die *ich* in
   die Roast-Prompts gesetzt hatte — der Auftraggeber nannte die vier Werte
   *gleichrangig* (Einfachheit, Transparenz, Sicherheit, Anwenderfreundlichkeit). Ohne
   strikte Lexikografie gewinnt nicht, was auf Achse 1+2 minimal vorn liegt, sondern was
   alle vier *zusammen* am besten bedient — und das tut die Lösung, die die explizite
   Anforderung erfüllt.
2. **II ist nicht *netto* einfacher.** Es tauscht die Shadow-Mount-Maschinerie gegen einen
   **externen State-Lifecycle** (`project-id`-Erzeugung, `gc` für verwaiste Projekte,
   `relink` nach Umzug) — den Roast-3 selbst als „deferred/unscoped" rügt. Komplexität wird
   *verschoben*, nicht beseitigt.
3. **TODO 7 fragt nach genau dem, was der Shadow-Mount liefert.** „der Agent darf diesen
   Ordner nicht lesen aber alle anderen … geht das irgendwie?" — der Nutzer ist *unsicher*
   beim Bind-Mount und bittet um eine sichere Mechanik, **nicht** darum, die Dateien
   auszulagern. Der Shadow-Mount *ist* das „überleg dir was".
4. **Die Mechanik ist ein Standard-Idiom, kein Glücksspiel.** tmpfs-über-Unterpfad zum
   Maskieren eines Unterordners (so wie man `node_modules`/`.git` maskiert) ist gängige,
   dokumentierte Docker-Praxis; §4.5 T2 verifiziert sie auf der gepinnten Version, §4.5 nennt
   den Pre-Start-Mount-Fallback für den Rand-Fall.

**Entscheidung: Option I ist der *einzige* Default und der einzige voll gepflegte Pfad.**
Option II ist **keine** koexistierende, gleichwertig getestete Architektur mehr (das wäre
die von Roast-3 zu Recht gerügte doppelte Pflege-Last), sondern eine **dokumentierte
Notluke** in einem Absatz (§4.7) für Nutzer, die den In-Tree-Ansatz bewusst ablehnen — ohne
eigenen Test-/Lifecycle-Apparat im Plan. Damit ist *eine* Topologie gewählt, die explizite
Anforderung erfüllt, und die Zwei-Pfad-Komplexität vom Tisch.

> Die §§4.1–4.6 beschreiben **Option I**. §4.7 skizziert die Notluke II als Abweichung.

### 4.1 On-Disk-Layout im Zielordner (Option I)

```
<zielordner>/.catraz/
├── .env                     # Secrets + aufgelöste Knöpfe (gitignored, 0600)
├── compose.override.yml     # optional, host-editierbar (§4.4)
├── config/                  # editierbare Kopien der Vorlagen (read-only gemountet)
│   ├── warden.toml  ·  allowlist.txt  ·  squid.conf
├── claude/                  # Claude-Home-Quellen (s. u.)
│   ├── .credentials.json    #   subscription: vom Host gesynct (RO in den Container)
│   └── .claude.json         #   subscription/api_key: IMMER von init/sync materialisiert
├── state/warden/            # SQLite-Quoten-State
├── run/warden/              # Admin-Unix-Socket (admin.sock) — Audit-Viewer, kein admin-net
└── logs/{warden,squid}/     # Audit-Logs
```

Das ist 1:1 das alte „On-Disk-Layout" aus README §5 — nur **eine Ebene tiefer**, unter
`.catraz/` statt im Repo-Root. **Wichtig (Roast-3 #4/#5):** `claude/` muss host-seitig
**beide** Dateien enthalten, weil der Container sie als RO-Einzeldateien bindet (§4.3) — eine
fehlende Bind-Quelle lässt `docker compose up` scheitern. `.credentials.json` kommt im
Subscription-Modus vom `sync`; `.claude.json` wird von `init`/`sync` **immer** angelegt
(Host-Kopie falls vorhanden, sonst der Onboarding-Default, §6.4). Im `api_key`-Modus enthält
`claude/` nur `.claude.json`.

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
| **Warden** | `config/warden.toml` · `state/warden` · `logs/warden` · `run/warden` | RO · RW · RW · RW | Trust-Boundary, hält Tokens ohnehin; `run/warden` = Admin-Unix-Socket (§4.4) |
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

#### Das Claude-Home — *eine* kohärente Mount-Topologie (Roast-2 #1, BLOCKER behoben)

Roast-2 hat zu Recht einen Widerspruch gefunden: §4.3 sagte „Rest tmpfs, aus
*image-gebackenen* Quellen befüllt", aber `.claude.json` ist **pro Nutzer** (es trägt
`organizationUuid`/`passesEligibilityCache` vom Host-`sync`, entrypoint.py:48–53) und
*nicht* im Image; §6.3 sagte zugleich „Verzeichnis bleibt in beiden Modi gemountet". Drei
Abschnitte beschrieben drei Topologien. Hier die **eine** verbindliche — gilt für §4.1,
§4.3, §6.3, §9 gleichermaßen:

```yaml
    volumes:
      # Die ZWEI per-User-Dateien einzeln read-only (lesbar, nicht überschreibbar):
      - { type: bind, source: ${PROJECT_DIR}/.catraz/claude/.credentials.json,
          target: /home/dev/.claude/.ro/.credentials.json, read_only: true }
      - { type: bind, source: ${PROJECT_DIR}/.catraz/claude/.claude.json,
          target: /home/dev/.claude/.ro/.claude.json,      read_only: true }
      # Der GESAMTE Rest des Homes ist tmpfs (flüchtig, kein Host-Bind):
      - { type: tmpfs, target: /home/dev/.claude }
```

Der **entrypoint** baut das Home bei jedem Start im tmpfs auf — die einzige korrekte Stelle,
weil tmpfs leer startet (Roast-2: `ensure_settings`' „return if exists" greift hier nie):

1. `.credentials.json` und `.claude.json` aus `…/.ro/` ins tmpfs-Home **kopieren** (nicht
   patchen-in-place — die RO-Mounts wären `EROFS`, das war der von Roast-2 entdeckte latente
   Bug an entrypoint.py:97). Erst die *Kopie* wird ge-`write_text`-patcht
   (`bypassPermissionsModeAccepted` etc.).
2. `CLAUDE.md` und `settings.json` aus image-gebackenen Quellen **immer überschreiben**
   (image-baked, per-User-neutral — anders als `.claude.json`).
3. `rc-debug.log` landet im tmpfs. **Bewusste Entscheidung (Roast-2):** der RC-Debug-Log ist
   pro Lauf flüchtig; wer ihn host-persistent braucht, setzt `--debug-file` auf einen
   Warden-/Logs-Pfad. (Heute lag er versehentlich im Bind — die Flüchtigkeit ist jetzt
   *gewählt*, nicht stilles Regress.)

**Sicherheits-Folge:** Was der Agent ins Home schreibt (bösartiges `settings.json` mit
Hooks, ein Symlink, was auch immer) lebt im flüchtigen tmpfs und ist beim nächsten Start
weg; die zwei per-User-Dateien sind RO und nicht vergiftbar. **Kein Persistenz-Pfad** von
Lauf zu Lauf, und der Agent schreibt **nie** ins Host-`.catraz` (auch nicht via Home).

> **`api_key`-Modus:** beide RO-Credential-Mounts entfallen; das tmpfs-Home wird nur mit
> `.claude.json`-Onboarding-State (image-baked Default) + `settings.json` befüllt. Der
> Schlüssel kommt als Env (§6). Damit ist `.catraz/claude/` in diesem Modus auf dem Host
> **leer** — `doctor auth` erzwingt das (§6.2).

#### Symlinks lösen sich im Container-Namespace auf — kein Host-Escape (Roast-1 #5, Teil-Rebuttal)

Ein in `/workspace` oder im Claude-Home liegender Symlink `evil -> /` oder `-> ../../`
zeigt im Container auf **Container**-`/` bzw. **Container**-`/home` — die Auflösung
geschieht im Mount-Namespace des Containers, nicht auf dem Host. Ein Symlink *kann* dem
Agenten also keinen Host-Pfad erschließen, den er nicht ohnehin gemountet hat. Der einzig
reale Effekt — ein Symlink aus `/workspace` auf `/home/dev/.claude/.credentials.json` —
gibt ihm Lesezugriff auf *seine eigene*, ohnehin in-process gehaltene Credential. Das
red-team-Spec (§4.5) schreibt genau diese Fälle als Negativtests fest.

**Aber: der Mount-*Quellpfad* wird host-seitig aufgelöst (Roast-2 #3).** Symlinks *im
Inhalt* sind harmlos (s. o.); ein Symlink **im Quellpfad** eines Binds löst Docker dagegen
**auf dem Host** auf — ist `${PROJECT_DIR}` oder `${PROJECT_DIR}/.catraz` selbst ein
Symlink, bindet man unbeabsichtigt ein anderes Host-Ziel. Darum prüft `catraz up`
**host-seitig vor dem Compose-Aufruf**: `${PROJECT_DIR}` und `${PROJECT_DIR}/.catraz` müssen
*reale* Verzeichnisse sein (`Path.is_symlink()`/`realpath`-Vergleich), sonst Abbruch
fail-closed. Damit gilt die Symlink-Garantie sauber abgegrenzt: *in-container*-Auflösung ist
sicher, *host-seitige* Quellpfad-Auflösung wird vor dem Start verifiziert.

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
(`agent`/`warden`/`proxy`) bleiben die Nutzer-Schnittstelle. **Folgeänderung (Roast-3 #11):**
Die heutige Alias-Auflösung (04-cli §5.6) zeigt auf *feste* Container-Namen — sie muss auf
**Compose-Projekt + Service-Label** umgestellt werden (`docker compose ... logs <service>`),
sonst finden `logs`/`status` die Container nach dem Wegfall der festen Namen nicht mehr.

#### Audit-Viewer ohne `admin-net`: Unix-Socket statt fester IP

Damit parallele Sandboxes wirklich kollisionsfrei sind, reicht das Container-Namens-Namespacing
**nicht** — das frühere `admin-net` (festes Subnetz `172.31.0.0/24` + statische IP
`172.31.0.2` für den Audit-Viewer auf `:9090`) ist eine **daemon-globale** Ressource und würde
beim zweiten `up` kollidieren. Lösung: der Admin-/Viewer-Server bindet auf einen **Unix-Socket
pro Projekt** unter `.catraz/run/warden/admin.sock` (`admin-net` entfällt ganz). Kein Subnetz,
keine IP, kein Port → kollisionsfrei *by construction* (jeder Socket ist eine Datei im eigenen
`.catraz`, Container-Pfade sind per-Namespace). Der Agent mountet das Verzeichnis nie → keine
Route dorthin (strikt sicherer als der bisherige Admin-TCP). Host-Zugang über
**`catraz audit --web`**, das einen *ephemeren* `127.0.0.1`-Port auf den Socket forwarded und
den Browser öffnet (ersetzt den früheren `socat`-Tunnel). Umsetzung & Tests:
[`05-packaging/02-catraz-home.md`](./05-packaging/02-catraz-home.md) Commit 2.4.

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
Transparenz-Verlust (§7.1): die Invarianten sind explizit und maschinell verifiziert.
**Schema-Robustheit (Roast-2 #7):** Der Parser prüft gegen das **aufgelöste JSON**
(`docker compose config --format json`), nicht gegen YAML-Text, und wird über *dieselbe*
CI wie die T-Tests (unten) mit je einem known-good- und known-bad-Override abgesichert —
kein zweiter Versionszweig.

### 4.5 Verifikations-Spec: der Shadow-Mount ist *definiert* durch seine Negativtests (Option I)

> Gehört zu **Option I** (§4.0). Unter Option II entfallen T1–T4, T7, T8 — es gibt keinen
> Overdeck zu verteidigen; T5/T6 (Claude-Home) und T9 (Warden-State) bleiben in beiden.

Die §4.3-Garantie ist zu tragend, um sie zu *behaupten*. Sie wird als ausführbare
red-team-Suite (`tests/redteam/test_shadow_mount.py`) geschrieben — **bevor** der
Mount-Code steht — und gilt nur als „funktionierend", wenn alle Fälle grün sind:

| # | Negativtest (im Agent-Container, sofern nicht anders vermerkt) | erwartetes Ergebnis |
| - | -------------------------------------------- | ------------------- |
| T1 | `ls -A /workspace/.catraz` | leer |
| T2 | Mount-Typ von `/workspace/.catraz` (`findmnt -no FSTYPE`) | `tmpfs`, nicht der Bind |
| T3 | Agent schreibt `/workspace/.catraz/x`; danach Host-`.catraz` prüfen | Host unverändert, kein `x` |
| T4 | `umount /workspace/.catraz` als `dev` | scheitert (`EPERM`) |
| T5 | `cat … > /home/dev/.claude/.ro/.credentials.json` (überschreiben) | scheitert (RO) |
| T6 | Agent schreibt `~/.claude/settings.json`; Container neu starten; Datei prüfen | vom entrypoint überschrieben, keine Agent-Hooks überleben |
| T7a | Symlink `/workspace/link -> /` bzw. `-> /home/dev/.claude`; auflösen | bleibt im Container-Namespace, kein Host-Pfad |
| T7b | **host-seitig:** `${PROJECT_DIR}`/`.catraz` als Symlink → `catraz up` | bricht fail-closed ab (§4.3 Quellpfad-Guard) |
| T8 | `grep` in `/proc/self/mountinfo` nach einem *erreichbaren* Host-Secret-Pfad | keiner erreichbar (Topologie sichtbar, Reichweite nicht) |
| T9 | nach `down`+`up --build` während Warden-Schreibvorgang: Quota-DB lesbar | konsistent oder fail-closed, nie fail-open (§7.2) |

Diese Tabelle *ist* die Spezifikation. Findet ein Test eine Lücke, ist der Shadow-Mount
nicht fertig — nicht das Dokument wird angepasst, sondern der Mount.

**Eine gepinnte Version statt einer Matrix (Roast-2 #8).** catraz ist ein
Einzel-Operator-Werkzeug auf *einer* Docker-Installation — eine Versions-*Matrix* für 9
Tests wäre Gold-Plating. Stattdessen: `doctor docker` erzwingt eine **Mindest-Docker-/
Compose-Version** (gegen die die T-Tests laufen) und **verweigert den Start darunter**.
„Wir haben Version X getestet und starten unter X nicht" ist ehrlicher und billiger als ein
getestetes Kreuzprodukt. Falls die gepinnte Version die Langform-tmpfs-Ordnung *nicht*
deterministisch liefert (T2 rot), fällt die Entscheidung auf einen expliziten
Pre-Start-Mount (catraz mountet das tmpfs via `docker run`-Flags statt Compose) — als
dokumentierter Fallback, nicht als Default.

### 4.6 `.gitignore`-Hygiene

`init` trägt `.catraz/` in die `.gitignore` des Zielordners ein (oder, wenn keine da ist
bzw. nicht gewünscht, in `.git/info/exclude`). So nistet sich catraz ein, ohne das
Projekt-Repo zu verschmutzen — wie ein gut erzogenes Tool.

### 4.7 Notluke: externer State (`--external-state`) — dokumentiert, nicht voll gepflegt

Wer den In-Tree-Ansatz bewusst ablehnt (z. B. weil das Projekt auf einem Dateisystem liegt,
auf dem der tmpfs-Overdeck-Test T2 rot ist, oder aus reiner Vorliebe für „keine Secrets im
Projektbaum"), kann `catraz init --external-state` wählen. Dann liegt im Projekt nur ein
fast leerer `.catraz/`-Marker (`project-id` + `.gitignore`), und der echte State unter
`~/.local/state/catraz/<project-id>/`. Der `/workspace`-Bind enthält dann **keine** Secrets
→ **kein Shadow-Mount, keine T1–T4/T7/T8, keine geschachtelt-Guard** nötig; die
Claude-Home-Härtung (§4.3 „Claude-Home"), Auth (§6) und Compose-Invarianten (§4.4) gelten
unverändert.

**Bewusst als Notluke, nicht als zweiter Default:** Der externe State-Lifecycle (stabile
`project-id`, `catraz gc` für verwaiste Projekte, `relink` nach Projekt-Umzug) ist hier
**nicht** voll ausspezifiziert — er wäre erst zu schließen, wenn jemand die Notluke wirklich
braucht. Das hält die Pflege-Last bei *einer* getragenen Topologie (Option I) und verschiebt
TODO 6/7s „alles in `.catraz`" nicht still nach außen, sondern nur auf ausdrücklichen Wunsch.

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

`doctor base` prüft den Base-Vertrag *laut* (`command -v apt-get` + `python3 --version`).
Der **setuid/setgid-Scan** (`find / -perm /6000`) und der **non-root-`USER`-Check** laufen
dagegen gegen das **finale, zusammengesetzte Image** (Base **+** Claude-Layer), nicht gegen
die Base allein (Roast-3 #7) — sonst entginge ein setuid-Binary, das erst ein vom
Claude-Layer installiertes apt-Paket mitbringt. Das fängt die zwei Dinge ab, die der
Claude-Layer *nicht* neutralisieren kann (s. §5.5). Befund → ⚠️/❌ mit Pfadliste.

### 5.5 „Claude-Layer oben" schützt gegen *versehentliche* Fehlkonfiguration — nicht gegen eine *feindliche* Base (Roast-2 #4)

Die vorige Fassung verkaufte A („Claude-Layer `FROM ${BASE}`") als „secure-by-construction,
egal was die Base tat". Das war **überzogen**. „Letzte Schicht gewinnt" gilt für `USER`,
`ENTRYPOINT`, `WORKDIR`, `ENV` — **nicht** für eine feindliche Lieferkette:

- **setuid-Binaries** aus der Base überleben; `USER dev` findet sie und eskaliert.
- Der Claude-Layer baut **mit den Binaries der Base** (`curl … | bash`, `apt`, `npm` laufen
  *auf* der Base) — eine kompromittierte Base-`curl`/`apt` unterwandert schon den Build.
- Gepflanzte `~/.bashrc`, `/etc/ld.so.preload`, Base-`ENV` persistieren.

**Ehrliche Einordnung:** Die Base ist in **A *und* B vertraut**. A schützt nur davor, dass
der Nutzer das Härten *vergisst* (er kann `USER dev` nicht versehentlich aufheben); gegen
eine *bösartige* Base schützt **keiner** von beiden — dort bleibt nur `doctor base`
(setuid-/USER-Scan, §5.4) und schlicht: keine unvertrauten Bases verwenden.

| Ansatz | Builds | schützt vor *versehentlichem* Nicht-Härten? | schützt vor *feindlicher* Base? |
| ------ | ------ | ------------------------------------------- | ------------------------------- |
| **A. Claude-Layer oben, `FROM ${BASE}`** (Default) | zwei | **ja** — catraz besitzt die letzte Schicht | **nein** (Base vertraut) |
| **B. publizierte `claude-base`, Nutzer `FROM`t** | einer | **nein** — Nutzer-Schicht ist letzte | **nein** (Base vertraut) |

Damit bleibt **A Default** — auf der *einen* Achse, auf der sie sich unterscheiden
(versehentliche Fehlkonfiguration), ist A besser, und TODO 2 verlangt „die Sicherheit, dass
das richtig gemacht wird". B koexistiert als einfacherer Ein-Phasen-Pfad; in **beiden** Fällen
prüft `doctor` die aufgelösten Compose-Invarianten (§4.4) und scannt die Base (§5.4).

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
  Compose-Profil oder eine von catraz gesetzte leere/gefüllte Variable). Die Home-Mounts
  folgen **genau** der einen Topologie aus §4.3 (zwei RO-Einzeldateien + tmpfs-Rest im
  Subscription-Modus; nur tmpfs-Home im `api_key`-Modus) — kein RW-Verzeichnis-Mount mehr.
- **`sync`**: liest die Quelle aus `CLAUDE_CREDENTIAL_SOURCE` statt fest aus `~/.claude`.
  **Achtung (Roast-2 #10):** `entrypoint.py cmd_sync` (entrypoint.py:29) kodiert die Quelle
  heute **hart** als `Path.home()/".claude"` und ignoriert ein Quell-Argument — der
  04-cli-`--from`-Flag läuft derzeit ins Leere. Die Umsetzung muss `cmd_sync` einen echten
  Quell-Parameter geben, nicht nur umbenennen. `up`/`init` rufen `sync` im Subscription-Modus
  automatisch, wenn die Credential fehlt.

### 6.4 Entrypoint-Umbau & `.claude.json`-Provisionierung (Roast-3 #3/#4/#6)

Die §4.3-Home-Topologie (RO-Einzeldateien + tmpfs-Home) ist mit dem **heutigen**
`entrypoint.py` *nicht* ohne Umbau vereinbar — Roast-3 hat die drei Stellen präzise benannt.
Der Plan macht den Umbau explizit, damit er nicht als „rewrite by implication" untergeht:

1. **`.claude.json`-Zielpfad ist das Home-*Root*, nicht das `.claude`-Verzeichnis.** Claude
   Code erwartet die Datei als `~/.claude.json` (Geschwister von `~/.claude/`, so der
   heutige Docstring entrypoint.py:59–66). Da `~/.claude` jetzt **tmpfs** ist, **entfällt der
   Symlink-Trick** (entrypoint.py:73–80): der entrypoint **kopiert** `…/.claude/.ro/.claude.json`
   nach `/home/dev/.claude.json` (Home-Root, image-Layer, beschreibbar) und patcht *dort* die
   Felder (`bypassPermissionsModeAccepted`, `remoteDialogSeen`, `hasTrustDialogAccepted`).
   `.credentials.json` dagegen kopiert er nach `~/.claude/.credentials.json` (im tmpfs).
   Beide Quellen sind RO unter `…/.ro/` → **kopieren, dann patchen**, nie in-place (das war
   der `EROFS`-Bug an entrypoint.py:97).
2. **„falls fehlend"-Guards fallen.** `ensure_settings` (entrypoint.py:116–117, `return if
   exists`) und die analoge Logik werden auf **unbedingtes Überschreiben** umgestellt —
   `settings.json` und `CLAUDE.md` sind image-baked und je Start frisch (auf tmpfs sind sie
   ohnehin jedes Mal weg; die Guard-Entfernung macht die Absicht *explizit*).
3. **`.claude.json` wird *immer* provisioniert — der lückenschließende Punkt.** Die RO-Bind-
   Quelle `…/.catraz/claude/.claude.json` **muss** vor `up` existieren, sonst scheitert der
   Mount. Heute kopiert `cmd_sync` `.claude.json` nur *falls auf dem Host vorhanden*
   (entrypoint.py:50–53) — auf einer frischen Maschine fehlt sie. Darum: **`init`/`sync`
   materialisieren `.catraz/claude/.claude.json` immer** — Host-Kopie falls vorhanden, sonst
   der Onboarding-Default (`{"hasCompletedOnboarding": true, …}`, den `ensure_claude_json`
   heute schon inline kennt, entrypoint.py:76–78). Im `api_key`-Modus gibt es **keinen**
   image-gebackenen `.claude.json` (das wäre ein erfundenes Asset wie das frühere
   `install-node.sh`) — der entrypoint **synthetisiert** ihn inline aus genau diesem Default.

Damit ist der Subscription-Pfad auf einer sauberen Maschine baubar, der `api_key`-Pfad hat
keine dangling-Asset-Abhängigkeit, und alle vier Home-Beschreibungen (§4.1, §4.3, §6.3, §9)
meinen dieselbe Topologie.

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

### 7.1 Transparenz trotz versteckter Assets (Roast-1 #10, verschlankt in Roast-2 #5/#6)

`--print` zeigt die *Invocation*, nicht den *Inhalt* der nun im Cache liegenden
Compose-/Dockerfiles. Für ein Werkzeug, dessen Seele die *nachvollziehbare* Isolation ist
(04-cli §7), ist das zu wenig. **Ein** Befehl deckt das ab — keine N-fache Taxonomie, keine
stale Datei:

- **`catraz show compose`** druckt das Compose-Asset (statisch, aus dem Cache).
- **`catraz show resolved`** läuft `docker compose config` **live** und druckt die
  *effektive* Topologie nach `.env`- und Override-Merge — **derselbe Code-Pfad** wie der
  `doctor`-Invariantencheck (§4.4), nicht eine zweite Materialisierung.

Roast-2 #5 zu Recht: eine bei `init` geschriebene `.catraz/compose.resolved.yml` **lügt**,
sobald `.env`/Override sich ändern — darum **gestrichen**; die aufgelöste Sicht ist immer
live. Roast-2 #6: die `show`-Ziele bleiben auf `compose`/`resolved` beschränkt; den Rest
(`claude-layer`, Dockerfiles, Warden-Quelle) erreicht man über den **dokumentierten
Cache-Pfad** (`catraz cache-dir` druckt ihn, dann `cat`/`ls`) — keine zu pflegende
Schlüsselwortliste.

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
| **Topologie-Wahl** | In-Tree-`.catraz` zieht Shadow-Maschinerie nach; extern wäre auf den ersten Blick einfacher. | **Entschieden (§4.0): Option I**, weil II die explizite TODO-6/7-Anforderung („Claude-Ordner & Logs *in* `.catraz`") verletzt und eigenen externen-State-Lifecycle nach sich zöge. II nur als Notluke `--external-state` (§4.7), nicht co-gepflegt. |
| **Provisionierung `.claude.json`** | RO-Bind-Quelle muss vor `up` existieren; `cmd_sync` legt sie heute nur optional an. | `init`/`sync` materialisieren sie **immer** (Host-Kopie oder Onboarding-Default); api_key synthetisiert inline (§6.4). |
| **Shadow-Mount-Robustheit** (nur Option I) | Garantie ruht auf non-privileged + non-root + Mount-Ordering. | Als **Spec** festgeschrieben (§4.5 T1–T9), nicht behauptet; Langform-tmpfs (§4.3); **eine gepinnte** Docker-Version statt Matrix. |
| **Claude-Home-Topologie** | drei Abschnitte beschrieben drei Mounts (Roast-2 #1). | **Eine** Topologie festgezurrt: 2× RO-Einzeldatei + tmpfs-Rest, entrypoint *kopiert dann patcht* (kein `EROFS`); §4.3/§6.3 synchron. |
| **mountinfo-Topologie** | Host-Pfade in `/proc/self/mountinfo` sichtbar. | Akzeptiert: Topologie ≠ Reichweite; T8 verlangt „kein *erreichbarer* Secret-Pfad". |
| **Override löst Grenze auf** | `compose.override.yml` könnte R6 brechen. | `doctor` prüft *aufgelöstes JSON* nach Merge (§4.4), abgesichert per known-good/-bad in derselben CI. |
| **Asset-Auflösung im Wheel** | `docker build` braucht reale Pfade; zip-Installs haben keine. | Deterministische Extraktion nach `~/.cache/catraz/<version>/`; `warden/`+`forward-proxy/` als Wheel-Includes (§3.1). |
| **Image-Schichtung** | Zwei-Phasen-Build; „secure by construction" war überzogen. | content-adressierter Tag + Phasen-Fehlermeldung (§5.2); §5.5 ehrlich: A schützt vor *versehentlichem*, nicht *feindlichem* Base; `doctor base` scannt setuid/USER (§5.4). |
| **Base-Vertrag** | Fremde Base ohne apt/python3. | Ehrlich verengt auf Debian/Ubuntu+glibc+python3; `doctor base` prüft laut (§5.4). |
| **Geschachtelte `.catraz`** (nur Option I) | Falscher, größerer Mount-Root exponiert Geschwister. | `find_root` bricht fail-closed bei verschachteltem `.catraz` ab (§4.2). |
| **Recreate vs. Warden-Schreiben** | WAL-Korruption, fail-open der Quota. | Graceful `SIGTERM`, WAL crash-konsistent, Warden fail-closed bei unlesbarem State (§7.2, T9). |
| **Migration halb fertig** | Alt-`./claude` unüberdeckt im Mount. | `up` verweigert bei Alt-Layout-Resten; atomic rename; Präzedenz `.catraz` (§8). |
| **Transparenz versteckter Assets** | Compose/Dockerfile nicht mehr im Klon sichtbar. | `catraz show compose`/`show resolved` (live, geteilter Pfad mit §4.4); **keine** stale Datei (§7.1). |
| **`CLAUDE_CREDENTIAL_SOURCE` / `cmd_sync`-Quelle** | Tilde-Expansion; `cmd_sync` hat heute keine Quell-Param. | catraz expandiert selbst; `cmd_sync` bekommt echten Quell-Parameter (§6.3, Roast-2 #10). |

### Offen / bewusst dem Implementierungs-Spike überlassen

- **T2-Spike vor dem Bau:** Die einzige verbliebene tragende, *noch nicht ausgeführte*
  Annahme ist die deterministische tmpfs-über-Unterpfad-Ordnung auf der gepinnten
  Docker-Version (§4.5 T2). Sie wird **zuerst** verifiziert; ist sie rot, greift der
  Pre-Start-Mount-Fallback (§4.5) — die Topologie-Entscheidung (Option I) kippt deshalb
  *nicht*, nur die Mount-Mechanik wechselt. (Die Notluke II bleibt davon unberührt.)
- **Notluke-II-Lifecycle:** `project-id`/`gc`/`relink` (§4.7) werden erst spezifiziert, wenn
  `--external-state` real nachgefragt wird — bewusst nicht jetzt, um die Pflege-Last bei
  *einer* Topologie zu halten.

---

## 10. Umsetzungsreihenfolge (inkrementell, kein Big-Bang)

Jeder Schritt ist für sich nützlich und unabhängig testbar.

| Schritt | Liefert | TODO |
| ------- | ------- | ---- |
| **1. Paketierung** | `pyproject.toml`, `src/catraz/`-Layout, `entrypoint.py`+`AGENT.md` als Assets, Root-Shim. `uv tool install` funktioniert, Verhalten unverändert. | 3, 4, 5 |
| **2. `.catraz/`-Heim** | `init`/`doctor`/Pfade auf `.catraz/` (Option I, §4.0); `--project-directory`/`--env-file`; `.gitignore`-Eintrag; `migrate`. | 6 |
| **3. Shadow-Mount** | **T2-Spike zuerst** (§4.5), dann tmpfs-Overlay auf `/workspace/.catraz`, Quellpfad-Symlink-Guard; **Red-Team T1–T9** als Spec. | 7 |
| **4. Auth + Entrypoint-Umbau** | `AUTH_MODE`-XOR (`doctor`/compose); RO-Home-Topologie + Entrypoint-Umbau & `.claude.json`-Provisionierung (§6.4); `CLAUDE_CREDENTIAL_SOURCE`; Auto-`sync`. | 1 |
| **5. Image-Schichtung** | Claude-Layer-Dockerfile `FROM ${BASE_IMAGE}`; Default-Base unter `assets/bases/`; `BASE_IMAGE`/`BASE_DOCKERFILE`-Modi; `doctor base`. | 2 |
| **6. Lokaler Modus** (§11) | `up` auf Infra-only + `remote`-Profil; entrypoint-`local`-Exec; `catraz local` (immer-Invariantencheck, fail-closed, `run --rm --no-deps`, Workdir/TTY/Exit-Pass); ehrliche `--help`-Grenze. | — (neu) |

Reihenfolge-Logik: Erst die **Hülle** (Paket + `.catraz/`), weil sie die Pfade definiert,
auf denen alles andere aufsitzt. Dann der **Sicherheits-Kern** (Shadow-Mount), weil er den
im TODO benannten Hauptzweifel auflöst. Dann **Auth** und **Image-Schichtung** als
unabhängige Komfort-/Flexibilitäts-Schichten obendrauf.

---

## 11. Lokaler Modus — `catraz` als drop-in `claude` mit Sicherheitsnetz

> Stand: Erstkonzept, danach in **Roast-4** gehärtet (s. [`roastiteration-4.md`](./roastiteration-4.md)).

### 11.1 Motivation: zwei Wege, denselben Käfig zu fahren

Bisher fährt man den eingesperrten Agenten **nur** über Remote Control (Daemon, von
claude.ai getrieben). Das ist stark für „autonom im Hintergrund", aber schwer für „ich sitze
am Terminal und will *jetzt* kurz mit Claude im Sandbox-Kontext arbeiten".

**Lokaler Modus** schließt die Lücke: man ruft Claude **direkt aus der Shell**, der Container
ist ein transparenter Sandbox-Mantel. So transparent, dass

```bash
alias claude='catraz local'
```

das normale `claude` ersetzt — und man **dasselbe Sicherheitsnetz** (Warden, Squid,
Netz-Isolation, kein GitLab-Token im Agenten) auch bei lokaler Arbeit immer anhat. Der
Käfig ist nicht mehr nur etwas für den autonomen Agenten, sondern für *jede* Claude-Sitzung.

### 11.2 Modell: persistente Infra, ephemerer Agent

Der Trick für „schnell" liegt in der Lebensdauer-Trennung:

| Dienst | Lebensdauer | warum |
| ------ | ----------- | ----- |
| **Warden + Squid** | **langlebig** — einmal hoch, bleiben (bis `catraz down`) | sie sind die zustandsbehaftete, langsam bootende Vertrauensgrenze (SQLite, Squid-Config, Healthchecks) |
| **Agent** | **pro Aufruf** — `docker compose run --rm` | „Container nur bei Aufruf (neu) starten"; ein frischer Container je Aufruf ist sogar *sicherer* (kein Zustand zwischen Aufrufen im als bösartig angenommenen Container) |

Die Geschwindigkeit kommt daher, dass Warden/Squid **nicht** je Aufruf neu booten — nur der
dünne Agent-Container wird gestartet.

### 11.3 Der Befehl

```
catraz local [--] <claude-argumente…>
```

Die Claude-Argumente werden **unverändert durchgereicht** (`argparse.REMAINDER`). Damit das
sauber funktioniert (Roast-4 #4): `local` **erbt die globalen catraz-Flags *nicht*** (sonst
fräße `catraz`s `-C`/`--no-color` Argumente, die für `claude` gedacht sind). Alles nach
`local` gehört `claude`; catraz-eigene Optionen stehen **vor** `local`
(`catraz -C <dir> local …`). So trägt der Alias jede `claude`-Aufrufform
(`catraz local -p "fix the bug"`, interaktiv, `echo … | catraz local -p …`). Ablauf:

1. **`find_root`** (`.catraz` aufwärts) → Projektwurzel; Relativpfad CWD→Wurzel berechnen.
   **Fail-closed (Roast-4 #1):** Kein `.catraz` gefunden → **Fehler** (Hinweis auf
   `catraz init`), **niemals** stilles `exec` des Host-`claude`. Ein Alias, der unbemerkt
   *un*sandboxed durchfällt, wäre das schlechtest­mögliche Ergebnis.
2. **Sicherheits-Preflight läuft *immer* (Roast-4 #1):** Lokaler Modus ist ein *neuer
   Agent-Start-Pfad* und unterliegt darum demselben „security-Checks laufen immer"-Prinzip
   wie `up` (04-cli §5.3). Konkret vor **jedem** Aufruf: der **aufgelöste-Compose-
   Invariantencheck** (§4.4 — `agent-net internal`, kein Token-Env, nicht privileged,
   tmpfs-Shadow vorhanden) — er ist billig (ein `docker compose config`-Parse) und fängt
   einen nachträglich manipulierten `compose.override.yml` ab. **Nicht** gecacht.
3. **Infra sicherstellen:** sind Warden+Squid gesund, weiter. Sonst Infra hochfahren (die
   teuren `doctor`-Online-Proben — Token etc. — laufen nur auf diesem **kalten** Pfad,
   nicht bei jedem warmen Aufruf). `AUTH_MODE`-Check (§6.2): fehlt die Subscription-Credential
   → Auto-`sync` oder klarer Fehler.
4. **Agent one-off starten** — catraz hat die Infra-Gesundheit bereits sichergestellt, darum
   `--no-deps` (deterministisch, kein dep-Restart-Race durch `run`; Roast-4 #6):
   ```bash
   docker compose -f <asset> --project-directory <ziel> --env-file <ziel>/.catraz/.env \
     run --rm --no-deps --workdir /workspace/<relpath> agent  local -- <claude-argumente…>
   ```
   `run` instanziiert **dieselbe** Service-Definition wie `up` (Netze, Shadow-Mount,
   RO-Home, kein Token) — `local` kann **keine** boundary-relevante Service-Config
   überschreiben; catraz exponiert auf `local` weder `--network`/`--privileged`/`--volume`
   noch `--entrypoint`. Der entrypoint läuft im **`local`-Modus** (11.5).
5. **Exit-Code & Signale durchreichen** (`run` leitet SIGINT/SIGTERM an den Container-Prozess;
   `--rm` räumt auf).

### 11.4 Compose: Profil-Split Infra ↔ Agent-Daemon

Damit `catraz up` *nicht* den Agent-Daemon mitstartet (lokaler Modus will den Agenten nur
on-demand), wandert der Remote-Control-Daemon hinter ein Compose-**Profil**:

- **Infra** (Warden, Squid): kein Profil → `catraz up` startet sie (beide Modi teilen sie).
- **Agent-Daemon** (`claude remote-control`): Profil `remote` → nur `catraz up --remote`.
- **Lokaler Modus**: `catraz local` stellt die Infra selbst sicher (11.3 Schritt 3) und ruft
  dann `run --rm --no-deps agent`.

> **Bedeutet eine Re-Definition von `catraz up` (Roast-4 #5):** Bisher (04-cli §5.3, §10)
> startete `up` *alle drei* Dienste. Neu: `catraz up` = **nur Infra**; der Agent-Daemon
> kommt über `up --remote`. Das ist eine bewusste, hier dokumentierte Änderung der
> `up`-Semantik (kein stilles Abweichen) und zieht in den Rollout (§10) ein.

### 11.5 Entrypoint: ein lokaler Exec-Pfad (kohärent mit §6.4)

Es ist **derselbe** entrypoint und **dasselbe** Setup wie §6.4 (RO-Home kopieren-dann-patchen,
git-insteadOf-Warden, drop-to-dev) — nur der **finale Exec** verzweigt:

- Default: `claude remote-control …` (Daemon).
- `local`-Modus: `claude <durchgereichte Argumente>` — interaktiv mit TTY.

Ausgewählt über das `run`-Kommando (`… run … agent local -- <args>`). **Modus-abhängig
(Roast-4 #8):** Die RC-spezifischen JSON-Patches (`remoteDialogSeen`,
`bypassPermissionsModeAccepted`) gehören in den **Daemon**-Pfad; im `local`-Modus werden sie
*nicht* gesetzt (s. 11.6).

### 11.6 Der native Feel — und die ehrliche Permission-Wahrheit (Roast-4 #2/#3)

- **Workdir-Mapping**: CWD-Relativpfad → `--workdir /workspace/<relpath>`.
- **TTY**: interaktiv → TTY (Default von `run`); Pipe (`!isatty`) → `-T`.
- **Exit-Code & Signale** 1:1 durchgereicht.
- **Permission-Modus**: lokal sitzt ein Mensch am TTY → **normale Permission-Prompts**
  (nicht `bypassPermissions` wie der Daemon). `--yolo` für Daemon-Parität.

> **Wichtig — Prompts sind UX, *kein* Sicherheits-Mechanismus.** Die erste Skizze rahmte
> „normale Prompts" so, als schützten sie die gebundenen Dateien. Das ist im *eigenen*
> Threat-Model falsch: Permission-Prompts sind **Client-UI**; ein als bösartig angenommener
> (oder prompt-injizierter) Claude ignoriert sie und ruft Tools direkt. Prompts sind hier
> reine **Verhaltens-Parität** zum echten `claude` (fragt vor Edits) für den *ehrlichen*
> Fall — sie sind nicht das, was die Sicherheit trägt. Was die Sicherheit trägt, steht in
> 11.8.

### 11.7 Latenz & Nebenläufigkeit

- **Latenz** je Aufruf ≈ Container-Create + entrypoint-Setup + Node-Start (~1–3 s). Für eine
  **interaktive** Sitzung (einmal starten, dann unterhalten) vernachlässigbar; für viele
  **skript-Einzelschüsse** (`claude -p` in Schleife) spürbar. Ein warmgehaltener
  `exec`-Pfad (`--warm`) ist **bewusst aufgeschoben** (Roast-4 #5: kein zweites
  Ausführungsmodell auf Verdacht — erst wenn Latenz real stört).
- **Nebenläufigkeit**: mehrere `local`-Sitzungen (und der Daemon) teilen `/workspace` und den
  **globalen** Warden-Quoten-Pool (R5) — korrekt, denn es ist *ein* Operator mit *einem*
  Budget. Gleichzeitige Schreiber auf `/workspace` sind wie zwei parallele `claude` Sache des
  Nutzers.

### 11.8 Was der lokale Modus schützt — und was nicht (Anti-Theater, Roast-4 #2/#3)

Der Alias darf kein falsches Sicherheitsgefühl erzeugen. Klartext:

| schützt (Warden/Squid, *identisch* zum Remote-Modus) | schützt **nicht** (in **keinem** Modus) |
| ---------------------------------------------------- | --------------------------------------- |
| Kein GitLab-Token im Agenten (R6); git nur über Warden | Den gebundenen Working-Tree `/workspace` — der Agent darf ihn editieren (das *ist* der Zweck) |
| Egress nur über Squid-Allowlist; keine eigene Netz-Route | Exfiltration über *erlaubte* Domains (npm/github …) — begrenzt, nicht eliminiert (README §2) |
| Push nur auf `claude/*`, kein Merge, Quoten (R1–R5) | Die Claude-Credentials selbst (irreduzibel, README §2.1) |
| `.catraz` für den Agenten unlesbar (Shadow-Mount §4.3) | — |

Kurz: Lokaler Modus gibt der *lokalen* Claude-Nutzung **exakt** das Netz-/Git-Sicherheitsnetz
des Remote-Modus — nicht mehr, nicht weniger. Er macht Claude **nicht** ungefährlich für
deine Dateien und ersetzt **kein** Code-Review. `catraz local --help` sagt genau das, damit
der Alias ehrlich bleibt.
