# 08 — CLI-Rework

Drei Phasen, je ein eigener, grün-testbarer Schritt:

| Phase | Inhalt | Verhalten |
| ----- | ------ | --------- |
| **1 — flag scoping** (§1–§6) | `--dry-run`/`-y` an die Commands binden, die sie lesen | nur Parser-Oberfläche, **erledigt** ✅ |
| **2 — Verhaltensfixes** (§7) | `--dry-run` wirklich trocken · `BASE_IMAGE` immer auflösen · `status`-Exit-Code · `version`-Signatur | **gewollte** Verhaltensänderungen, je mit Test |
| **3 — Refactor** (§8) | `cli.py` (639 Z.) in Topic-Submodule, Dispatch-Tabelle, Dedup-Helfer | **keine** Verhaltensänderung (reine Umstrukturierung) |

**Reihenfolge: 2 vor 3.** Erst die kleinen Verhaltensfixes in der heutigen Struktur
(klein, lokal testbar), dann die mechanische Verschiebung in Module. So bleibt jeder
Commit reviewbar und der Refactor ist nachweisbar verhaltensneutral (Tests aus Phase 1+2
bleiben grün).

---

## Phase 1 — flag scoping

**Ziel:** Das CLI-Interface ehrlich machen. Flags sollen nur dort erscheinen, wo sie
auch wirken. **Keine Verhaltensänderung** — reines Re-Scoping der `argparse`-Deklaration
plus Hilfetexte. Jeder Codepfad hinter den Commands bleibt byte-identisch.

Bezug: `src/catraz/cli.py` (`add_global`, `_g`, `build_parser`, `main`).

---

## 1. Problem

`add_global()` hängt vier Flags an den Top-Parser **und** (über `_g()` →
`parents=[...]`) an **jeden** Subcommand. Zwei davon sind aber gar nicht global —
sie werden nur von genau einem Command gelesen:

| Flag | gelesen in | global deklariert? | wirkt überall? |
| ---- | ---------- | ------------------ | -------------- |
| `-C/--dir` | alle (`find_root`) | ja | ✅ korrekt global |
| `--no-color` | alle (`Out`) | ja | ✅ korrekt global |
| `--print/--dry-run` (`print_only`) | **nur** `up`/`down` (`cli.py:275,320`) | ja | ❌ no-op bei init/doctor/status/logs/sync/audit/prune/version |
| `-y/--yes` | **nur** `init` (`cli.py:118`) | ja | ❌ no-op bei allen anderen |

Folge: `catraz status --dry-run`, `catraz logs --yes`, `catraz audit --dry-run`
werden klaglos akzeptiert und tun **nichts**. Das Interface verspricht etwas, das es
nicht hält. Der vorhandene Hilfetext `"... without running it (up/down)"` ist bereits
ein Eingeständnis dieses Lecks — ein Flag, dessen Hilfe seinen eigenen Geltungsbereich
hineinschreiben muss, gehört nicht in den globalen Block.

### Nebenbefunde (mitnehmen, klein)

- **N1** — `version`-Subcommand-Hilfe sagt noch `"show CLI + component versions"`,
  obwohl die Component-Version-Anzeige entfernt wurde. → `"show CLI version"`.
- **N2** — `--print/--dry-run`-Hilfe enthält das manuelle `"(up/down)"`. Nach dem
  Re-Scoping überflüssig (das Flag steht dann nur noch an up/down) → entfernen.

### Bewusst **kein** Befund (nicht anfassen)

- **`run` ohne globale Flags.** `run` nutzt `nargs=REMAINDER`, damit *alles* nach
  `run` wörtlich an `claude` geht. Es trägt deshalb absichtlich keine `_g()`-Flags.
  `catraz -C /pfad run …` (global **vor** dem Subcommand) funktioniert; `catraz run
  -C …` reicht `-C` an claude durch. Das ist gewollt (drop-in `claude`) und bleibt.
- **`-V/--version` Flag *und* `version` Subcommand.** Doppelter Zugang zur selben
  Information, aber verbreitete Konvention (`git`, `docker`). Muscle-Memory schonen,
  beide behalten.

---

## 2. Designprinzip

> **Ein Flag steht genau an den Parsern, deren Handler es liest — nicht weiter.**

Daraus folgt eine saubere Zweiteilung:

- **Echt global** (jeder Command liest sie, sinnvoll vor *und* nach dem Subcommand):
  `-C/--dir`, `--no-color`. Bleiben in `add_global()` → bleiben auf Top-Parser und
  via `_g()` auf jedem Subparser.
- **Command-lokal** (genau ein Command liest sie, nur *nach* dem Subcommand):
  `--print/--dry-run` → nur `up` + `down`; `-y/--yes` → nur `init`.

`--print/--dry-run` und `-y/--yes` entfallen also aus `add_global()` und werden
direkt an den jeweiligen Subparsern deklariert. Damit gilt künftig z. B. `catraz up
--dry-run` (natürliche Form), während `catraz status --dry-run` mit einem klaren
`error: unrecognized arguments: --dry-run` **fail-loud** wird — genau das gewünschte
ehrliche Verhalten.

---

## 3. Konkrete Änderungen (`src/catraz/cli.py`)

### 3.1 `add_global()` — auf die zwei echten Globals reduzieren

```python
def add_global(parser):
    """Truly global flags — repeated on top parser and every subparser so they work
    before *or* after the subcommand. Command-specific flags (--dry-run, --yes) live
    on their own subparser instead, so they appear only where they actually act."""
    parser.add_argument("-C", "--dir", default=argparse.SUPPRESS,
                        help="project root (default: dir with .catraz/)")
    parser.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS,
                        help="disable ANSI colors")
```

`--print/--dry-run` und `-y/--yes` werden hier **gestrichen**.

### 3.2 `--print/--dry-run` an `up` und `down`

Identische Deklaration (gleicher `dest="print_only"`, gleiche Aliase), nur verschoben:

```python
pu.add_argument("--print", "--dry-run", dest="print_only", action="store_true",
                help="show the compose command without running it")
# … und an pdn analog
```

(Hilfetext ohne `"(up/down)"` — N2.)

### 3.3 `-y/--yes` an `init`

```python
pi.add_argument("-y", "--yes", action="store_true",
                help="non-interactive; keep existing .env values, skip prompts")
```

### 3.4 `main()` — Normalisierung bleibt unverändert tragfähig

`main()` macht heute schon:

```python
args.print_only = getattr(args, "print_only", False)
args.yes        = getattr(args, "yes", False)
```

Das `getattr(..., False)` ist genau der Mechanismus, der weiterhin trägt: Commands
ohne das Flag haben das Attribut nicht → `False`. **Diese zwei Zeilen bleiben
stehen** (sie sind jetzt die einzige Quelle des Defaults für Commands, die das Flag
nicht deklarieren). `args.dir`/`args.no_color` bleiben wie gehabt.

> Wichtig: Diese `getattr`-Defaults **nicht** entfernen. `cmd_up`/`cmd_down` lesen
> `args.print_only`, `cmd_init` liest `args.yes` — unverändert. Würde man die
> Normalisierung streichen, bräuchten die Handler eigene `getattr`-Guards. Einfacher
> ist: Zeilen lassen.

### 3.5 Hilfetext-Drift (N1 + N1b)

`cmd_version` druckt nur noch `catraz {__version__}` (Component-Versionen entfernt).
**Beide** stale „versions"-Plurale fixen — Subcommand-Hilfe *und* `-V`-Flag:

```python
p.add_argument("-V", "--version", action="store_true", help="show version and exit")  # war: "versions"
...
sub.add_parser("version", parents=[_g()], help="show CLI version")                     # war: "CLI + component versions"
```

### 3.6 `add_global`-Docstring entrümpeln (Finding 6)

Die SUPPRESS-Begründung („value given before the subcommand … clobbered") betrifft
nach dem Re-Scoping nur noch `-C`/`--no-color`. Den Docstring auf diese zwei Flags
umschreiben (siehe 3.1), damit die Erklärung nicht mehr Flags beschreibt, die gar
nicht mehr drin stehen — sonst genau der „Hilfe lügt über Scope"-Geruch, den wir
beseitigen.

---

## 4. Was sich beobachtbar ändert (ehrlich)

> **Klarstellung der Vorgabe.** „Keine Logikänderung" heißt: **kein Handler-Codepfad**
> ändert sich (byte-identisch), **kein erfolgreicher Befehl ändert sein Ergebnis-/
> Compose-Kommando**. Die Parser-**Oberfläche** ändert sich aber bewusst — das *ist*
> die Aufgabe. Drei Aufruf-Klassen verschieben sich; alle drei sind gewollt:

| Aufruf | vorher | nachher | Bewertung |
| ------ | ------ | ------- | --------- |
| `catraz up --dry-run` (post) | druckt Compose-Cmd | **identisch** | — |
| `catraz down --dry-run` (post) | druckt Compose-Cmd | **identisch** | — |
| `catraz init -y` (post) | non-interactive | **identisch** | — |
| `catraz status --dry-run` | exit 0, no-op | **exit 2** (unrecognized) | ✅ gewollt: fail-loud statt stilles No-op |
| `catraz logs --yes` | exit 0, no-op | **exit 2** | ✅ gewollt |
| **`catraz --dry-run up`** (prä) | exit 0, druckt | **exit 2** | ⚠️ bewusst aufgegeben — s. u. |
| **`catraz -y init`** (prä) | exit 0 | **exit 2** | ⚠️ bewusst aufgegeben — s. u. |
| `catraz -C X <cmd>` / `--no-color` (prä **und** post) | global | **identisch** | — |
| jeder Handler-Codepfad | — | **byte-identisch** | — |

### 4.1 Aufgabe der Prä-Subcommand-Form (Finding 1, der ernste Treffer)

Heute stehen `--dry-run`/`-y` **auch am Top-Parser** (via `add_global(p)`), deshalb
funktioniert die Form **vor** dem Subcommand: `catraz --dry-run up`, `catraz -y init`.
Nach dem Re-Scoping stehen sie nur noch am jeweiligen Subparser → die Prä-Form wird
`exit 2`. Das ist eine **echte, wenn auch winzige, Breaking-Change** für genau diese
zwei Formen.

**Entscheidung: bewusst akzeptieren, nicht reparieren.** Die natürliche, vom Auftrag
geforderte Form ist `catraz up --dry-run` (Flag *am* Command, der es liest). Die
Prä-Form *am Top-Parser* zu erhalten hieße, das Flag global zu lassen — genau das
Leck, das wir schließen. Man kann nicht „`catraz --dry-run up` erlauben, aber
`catraz --dry-run status` verbieten": der Top-Parser sieht das Flag, bevor der
Subcommand feststeht. Beides global oder beides lokal — wir wählen lokal.

`-C/--dir` und `--no-color` bleiben echt global (Prä **und** Post), weil jeder
Handler sie liest; nur die zwei Command-lokalen Flags verlieren die Prä-Form.

### 4.2 Bezug zu `04-cli.md`

`04-cli.md` §132/§89 sagt bereits: `--print` „wirkt nur bei Compose-aufrufenden
Befehlen (`up`/`down`)". Diese Änderung bringt die **Parser-Oberfläche in Deckung**
mit der dort schon dokumentierten Realität. Das Prinzip P4 (§107) ist aspirationell
weiter gefasst („jeder zustandsändernde Befehl"); `--print` auf `sync`/`prune`/`run`
auszuweiten wäre **neue Logik** und damit hier **out of scope**. Optionaler
Folge-Edit (kein Muss): P4-Zeile §107 an §132 angleichen.

---

## 5. Tests (`tests/cli/`)

Bestehende Tests müssen grün bleiben. **Wichtig:** `tests/cli/test_up_profile.py` baut
`args` von Hand per `SimpleNamespace` und ruft `cmd_up` direkt — es geht **nicht** durch
`build_parser()`/`parse_args` und fängt Scoping-Regressionen daher **nicht**. Die neuen
Tests müssen über `build_parser().parse_args([...])` (bzw. `main([...])`) laufen, sonst
ist die eigentliche Regression unsichtbar (Finding 5).

Neu hinzu (reine Parser-Assertions, kein Docker):

1. **Post-Form bleibt grün:** `up --dry-run`, `down --print`, `down --dry-run`
   → `args.print_only is True`; `init -y` → `args.yes is True`.
2. **Falscher Command fail-loud:** `status --dry-run`, `logs --yes`, `audit --dry-run`
   → `SystemExit` (Code 2).
3. **Prä-Form jetzt fail-loud (pinnt 4.1):** `--dry-run up`, `-y init`
   → `SystemExit` (Code 2). *Dieser Test ist die Absicherung gegen versehentliches
   Wieder-Global-Machen — er kodiert die bewusste Entscheidung.*
4. **Echte Globals unberührt, Prä + Post:** `-C /x status` **und** `status -C /x`
   → beide parsen; `--no-color status` und `status --no-color` → beide parsen.
5. **Default-Mechanik:** für Nicht-up/down-Commands gilt
   `getattr(args, "print_only", False) is False`; für Nicht-init analog `yes`.

Lauf: `uv run --with pytest python -m pytest tests/cli -q`.

---

## 5a. Ausdrücklich **kein** Ziel (Finding 7)

`logs` und `audit` deklarieren `-f/--follow` und `--tail` doppelt, und
`catraz logs --audit` dispatcht in denselben `_tail_audit` wie `catraz audit`. Echte
Redundanz — aber Zusammenlegen ändert Verhalten/Dispatch und ist damit **Logik**,
nicht Re-Scoping. Hier **nicht** anfassen; ggf. eigener Folge-Task.

---

## 6. Umsetzungsreihenfolge (ein Commit)

1. `add_global()` auf `-C`/`--no-color` reduzieren + Docstring (3.1, 3.6).
2. Flags an `pu`/`pdn`/`pi` deklarieren (3.2–3.3).
3. Beide `version`-Hilfetexte fixen — Subcommand *und* `-V`-Flag (3.5).
4. Tests aus §5 ergänzen (inkl. Prä-Form-Pin §5.3), grün fahren.

Conventional-Commit-Betreff: `refactor(cli): scope --dry-run/--yes to the commands
that use them`. Keine Trailer (Repo-Konvention, siehe `00-overview.md`).

---

# Phase 2 — Verhaltensfixes

Vier Änderungen, die **bewusst** Verhalten ändern (anders als Phase 1/3). Jede bringt
ihren Test im selben Commit mit (Konvention `00-overview.md`). Reihenfolge frei; ein
gemeinsamer Commit ist ok, da klein und thematisch verwandt.

## B1 — `--dry-run` nicht abbrechen / kein Docker, aber **fidel**

**Befund.** `cmd_down` ist schon trocken (berechnet `down_args`, dann `print_only`-Zweig,
*davor* keine Seiteneffekte). `cmd_up` **nicht**: der `print_only`-Zweig steht erst bei
`cli.py:275`, davor laufen unbedingt `(root/".catraz").mkdir()` (258),
`auth.write_auth_fragment(root)` (260), `assert_real_dirs`/`assert_invariants` (263–264).
`assert_invariants` ruft `docker compose config` und kann mit `EXIT_DOCTOR` **abbrechen** —
ein `--dry-run`, das Docker anfasst und scheitern kann statt zu drucken.

**Falle (Roast #1) — nicht naiv nach ganz oben ziehen.** `base_cmd` (`compose.py:42–43`)
hängt `-f .catraz/.auth.compose.yml` **nur an, wenn die Datei existiert**. `write_auth_fragment`
schreibt sie bei **jedem** echten `up` (immer, deterministisch aus `AUTH_MODE`). Würde der
`print_only`-Zweig *vor* `write_auth_fragment` stehen, ließe der **erste** Dry-Run (Fragment
noch nicht da) das `-f .auth.compose.yml` weg → das gedruckte Kommando wäre **nicht** das,
was ein echtes `up` ausführt. Genau die Onboarding-Lage, die zählt.

**Fix.** `mkdir` + `write_auth_fragment` **bleiben vor** dem `print_only`-Zweig (das Fragment
ist ein deterministischer, regenerierter, gitignorierter Laufzeit-Artefakt und gehört zum
Kommando — es zu schreiben ist auch im Dry-Run harmlos). Erst **danach** drucken/zurück; alles
Schwere/Validierende (auto-sync, Security-Preflight, `assert_*` mit `docker compose config`,
build, up) **entfällt** im Dry-Run:

```python
def cmd_up(root, args, out):
    up_args = (["--profile", "remote"] if args.remote else []) + ["up", "-d"]
    if args.build: up_args.append("--build")
    if args.pull:  up_args.append("--pull=always")
    # Fragment ist Teil JEDES echten up (base_cmd hängt -f an, wenn es existiert) →
    # ein fideler --print muss es widerspiegeln. Schreiben ist auch im Dry-Run benign.
    (root / ".catraz").mkdir(exist_ok=True)
    auth.write_auth_fragment(root)
    if args.print_only:
        compose_run(root, up_args, print_only=True)
        return EXIT_OK
    # ── ab hier Docker/validierend: auto-sync, preflight, assert_*, build, up ──
    ...
```

**Beobachtbarer Unterschied:** Dry-Run ruft **kein** `docker compose config` mehr auf und
**bricht nicht** mit `EXIT_DOCTOR` ab — es druckt immer. Das gedruckte Kommando ist **byte-
identisch** zu heute (Fragment in beiden Fällen geschrieben → `-f` in beiden Fällen drin).

> Bewusste Abweichung von „touch nothing" (meine ursprüngliche Empfehlung): Fidelität schlägt
> Null-Schreibzugriff. Das einzige geschriebene Artefakt ist das ohnehin bei jedem `up`
> regenerierte, gitignorierte `.auth.compose.yml`. Die *schädlichen* Effekte (Docker-Aufruf,
> Abbruch) sind weg — das war der eigentliche Punkt.

**Test:** (a) `compose_run`-Spy: `cmd_up(print_only=True)` → genau **ein** `compose_run` mit
`print_only=True`, **kein** `assert_invariants`/`run_doctor`-Aufruf (Spies zählen 0). (b)
Fidelität: mit *vorab gelöschtem* Fragment druckt der Dry-Run trotzdem die Variante **mit**
`-f .auth.compose.yml` (Fragment existiert nach dem Lauf). (c) `--dry-run` gibt `EXIT_OK`
auch dann, wenn `assert_invariants` (gepatcht zum Werfen) im Nicht-Dry-Run abbräche.

## B2 — `BASE_IMAGE` immer auflösen (latenter Onboarding-Bug)

**Befund.** `image.resolve_base()` läuft heute **nur** in `cmd_up` unter `args.build`
(`cli.py:281`). `.env` setzt `BASE_IMAGE` nicht mehr (Doc 07/diese Iteration), und
`docker-compose.yml` hat `BASE_IMAGE: ${BASE_IMAGE}` **ohne Default**. Compose baut ein
fehlendes Image automatisch → der **erste** `catraz run` und `catraz up --remote` *ohne*
`--build` bauen den Agent-Layer mit `FROM <leer>` → kryptischer Build-Fehler direkt nach
`init`. `cmd_run` löst die Base überhaupt nie auf. (Verifiziert: `compose.run` baut
`env = os.environ + PROJECT_DIR`; `BASE_IMAGE` kommt nur über `extra_env` rein.)

**Fix.** `image.resolve_base(root)` (content-adressiert, gecacht → no-op wenn vorhanden)
auflösen und als `extra_env={"BASE_IMAGE": …}` einspeisen — **inline, kein Helfer** (Roast #6:
ein `_build_env` in `stack.py` zwänge `run.py` zu einem topic-fremden Import; die eine Zeile
direkt am Aufrufort ist sauberer), **genau dort, wo der Agent-Layer gebaut werden kann**:

- `cmd_up`: `extra_env = {"BASE_IMAGE": image.resolve_base(root)} if args.remote else None`.
  Scoping: Der Agent (`claude-dev-env`) steht unter `profiles: ["remote"]`, wird also nur bei
  `up --remote` gebaut. Plain `up`/`up --build` (infra-only) braucht **keine** Base — heute
  löst `up --build` die schwere cpp/rust/python-Base *unnötig* auf; die Bindung an `args.remote`
  ist damit zugleich eine Korrektur.
- `cmd_run`: `extra_env = {"BASE_IMAGE": image.resolve_base(root)}` für den `run`-Compose-Aufruf.
  `docker compose run claude-dev-env` aktiviert den Service über den **Namen** (Profil egal) und
  baut ihn, falls fehlend → Base muss hier immer aufgelöst werden.
- `_ensure_infra`: **kein** Base — sein `up -d` (ohne `--profile remote`) startet nur
  warden+squid; die schwere Base hier aufzulösen wäre Verschwendung.

**Beobachtbar:** `catraz run`/`up --remote` bauen die Base bei Bedarf selbst, statt zu
scheitern. Erfolgreiche Pfade von heute bleiben identisch (Image schon da → `resolve_base`
no-op, gleiche Tags). **Akzeptierte Nebenwirkung (Roast #8):** `cmd_run` schält künftig pro
Aufruf ein `docker image inspect` (in `_image_exists`) ein — vernachlässigbar; und falls sich
der Base-Dockerfile-Hash geändert hat, löst ein nacktes `run` einen Voll-Rebuild aus (gewollt:
Base-Änderungen greifen, aber als UX hier erwähnt).

**Tests — neu *und* Bestand reparieren (Roast #2):**
- Neu: Spy auf `image.resolve_base` (→ `"catraz-base:test"`); `cmd_run` → `compose_run` bekam
  `extra_env={"BASE_IMAGE":"catraz-base:test"}`; `cmd_up(remote=False)` → `resolve_base`
  **nicht** aufgerufen; `cmd_up(remote=True)` → aufgerufen.
- **Bestand:** `tests/cli/test_up_profile.py::_mock_cmd_up` patcht heute `resolve_base` **nicht**.
  Mit B2 ruft `cmd_up(remote=True)` jetzt die *echte* `resolve_base` → echtes `docker build`/
  `FileNotFoundError` auf CI. `_mock_cmd_up` **muss** ein `monkeypatch.setattr(image,
  "resolve_base", lambda root: "catraz-base:test")` ergänzen (Patch am `image`-Modul-Singleton,
  überlebt den Phase-3-Move). Im selben Commit.

## B3 — `status`-Exit-Code spiegelt Gesundheit

**Befund.** `cmd_status` gibt **immer** `EXIT_OK` zurück (Z. 331/335/346) — auch wenn die
Stack nicht läuft oder ein Service ungesund ist. Als Health-Gate in Skripten/CI nutzlos.

**Fix.** Exit-Code an den Zustand koppeln:

| Lage | heute | neu |
| ---- | ----- | --- |
| nicht eingerichtet (kein `.env`) | `EXIT_OK` | `EXIT_OK` *(kein Fehler — nur „noch nichts da")* |
| eingerichtet, Stack nicht laufend | `EXIT_OK` | `EXIT_GENERAL` |
| laufend, **alle** `_row_ready` | `EXIT_OK` | `EXIT_OK` |
| laufend, mind. ein Service nicht ready | `EXIT_OK` | `EXIT_GENERAL` |

Ausgabe (Texte/URLs) unverändert — nur der Return-Wert. „Nicht eingerichtet" bleibt
bewusst `0` (ist kein Fehlerzustand, sondern der Normalfall vor `init`; ein Skript, das
*Health* prüft, läuft erst nach Setup).

> **Roast-Frage offen:** Soll „nicht laufend" wirklich `EXIT_GENERAL` (1) sein oder ein
> distinkter Code? `EXIT_GENERAL` reicht fürs Gating; ein Spezialcode wäre Over-Engineering.
> Entscheidung: `EXIT_GENERAL`.

> **Bekannte Unschärfe (Roast #7):** `compose_ps` (`compose.py:66–77`) schluckt sowohl
> „nichts läuft" als auch „Docker-Fehler/kaputte compose-Datei" in `[]`. Damit liefert ein
> Docker-Ausfall ebenfalls `EXIT_GENERAL` **mit** der Meldung „Stack is not running" — der
> Exit-Code ist fürs Gating korrekt (non-zero), die Meldung bei Infra-Fehler aber ungenau.
> **Nie false-healthy** (ein Docker-Fehler kann den „alle ready"-Zweig nicht erreichen), daher
> kein Sicherheitsproblem. Bewusst akzeptiert; eine genauere Fehlermeldung wäre ein Folge-Task.

**Test:** `compose_ps`-Spy. leere Liste → `EXIT_GENERAL`; alle ready → `EXIT_OK`; einer
„starting"/„unhealthy" → `EXIT_GENERAL`; kein `.env` → `EXIT_OK`.

## B4 — `cmd_version`-Signatur vereinheitlichen (Cosmetic, enables Phase 3)

`cmd_version(root, out)` weicht von allen anderen `cmd_*(root, args, out)` ab und nutzt
`root` nicht mehr (Pylance-Warnung). Auf `cmd_version(root, args, out)` vereinheitlichen
(`args`/`root` ungenutzt, aber uniforme Signatur) — Voraussetzung für die Dispatch-Tabelle
in Phase 3. Aufrufer (`-V`-Flag-Pfad **und** `version`-Subcommand) entsprechend anpassen.
Zusätzlich **`return EXIT_OK`** statt implizit `None` (Roast #9): heute druckt main den
Wert nicht aus, aber in der Dispatch-Tabelle wird `return HANDLERS[cmd](…)` direkt
zurückgegeben — `None` würde `sys.exit(None)` (=0) liefern, aber ein `assert main([...])
== EXIT_OK` bräche. Billiges Absichern. Beobachtbar: `print(f"catraz {__version__}")` bleibt.

---

# Phase 3 — Refactor (verhaltensneutral)

**Ziel:** `cli.py` (639 Z.) schrumpfen und Duplikation entfernen, **ohne** Verhalten zu
ändern. Maß: alle Tests aus Phase 1+2 + Bestand bleiben grün. Reine Code-Bewegung +
Helfer-Extraktion + Dispatch-Tabelle.

## 3.1 Modul-Aufteilung (Topics)

`cli.py` wird zur **dünnen Vordertür**: Parser + Dispatch. Die Handler ziehen in ein
`commands/`-Paket, nach Thema gebündelt:

```
src/catraz/
├── cli.py            # NUR: imports, _g/add_global/build_parser, main()-Dispatch, Re-Exports,
│                     #      cmd_prune + cmd_version (je <7 Z., kein Modul wert)
├── ui.py             # NEU: class Out (Styling) — rein präsentativ
└── commands/
    ├── __init__.py   # ggf. _rc-Helfer (oder in compose.py) — s. §3.3
    ├── setup.py      # cmd_init, cmd_doctor, cmd_sync, _run_sync, _auto_sync_if_needed, _ensure_gitignore
    ├── stack.py      # cmd_up, cmd_down, cmd_status, _wait_healthy, _row_ready, _print_urls, _security_preflight
    ├── run.py        # cmd_run, _oneoff_args, _ensure_infra
    └── observe.py    # cmd_logs, cmd_audit, _tail_audit, _UdsProxy
```

**Alle 11 Commands zugeordnet:** init/doctor/sync → `setup`; up/down/status → `stack`;
run → `run`; logs/audit → `observe`; prune/version → bleiben in `cli.py` (winzig).
Richtgröße danach: `cli.py` ≈ 170 Z., jedes `commands/*.py` 60–140 Z. Begründung der
Schnitte: `setup` = Erst-Einrichtung/Preflight/Credentials (doctor gehört dazu — `init`
ruft ihn am Ende); `stack` = Lebenszyklus + Health-Helfer; `run` = One-off-Agent;
`observe` = Logs/Audit/Viewer. (`_build_env` entfällt — B2 inlined `image.resolve_base`.)

## 3.2 Dispatch-Tabelle statt if/elif-Kette

`main()` hat eine 9-fache `if args.command == "x": return cmd_x(...)`-Kette (Z. 607–627)
plus einen **zweiten** `try/except`-Block extra für `init` (Z. 591–598). Ersetzen durch
eine Tabelle mit **uniformer** Signatur `(root, args, out)` (deshalb B4):

```python
HANDLERS = {
    "init": setup.cmd_init, "doctor": setup.cmd_doctor,
    "up": stack.cmd_up, "down": stack.cmd_down, "status": stack.cmd_status,
    "run": run.cmd_run, "logs": observe.cmd_logs, "audit": observe.cmd_audit,
    "sync": setup.cmd_sync, "prune": cmd_prune, "version": cmd_version,  # prune/version: lokal
}
```

`main()` löst `root` auf (Sonderfall `init`: nimmt `args.dir`/CWD statt aufwärts zu laufen
— diese eine Bedingung bleibt, nur **vor** dem gemeinsamen `try`), holt
`HANDLERS[args.command]` und ruft ihn in **einem** `try/except CliError/KeyboardInterrupt`.
Damit verschwindet der duplizierte Exception-Block. Verhalten (Exit-Codes,
KeyboardInterrupt→`print()`+`EXIT_GENERAL`) identisch.

## 3.3 Dedup-Helfer

- `_rc(r)` → `EXIT_OK if r and r.returncode == 0 else EXIT_GENERAL`. Heute wörtlich in
  `cmd_down` (325) und `cmd_logs` (413). **NUR diese zwei** in `compose.py` zentralisieren.
  **`cmd_run` ausdrücklich AUSGENOMMEN (Roast #5):** `cmd_run` gibt `r.returncode if r else
  EXIT_GENERAL` zurück — es **propagiert claudes eigenen Exit-Code** (2, 130, 137, …), der
  ganze Sinn des drop-in `alias claude='catraz run'`. `_rc` würde das auf 0/1 plätten →
  **Verhaltensänderung**. `cmd_run` behält seine eigene Rückgabezeile, kein `_rc`.
- `_security_preflight(root, out) -> bool` → **exakt** `print_findings(run_doctor(root,
  only=SECURITY_SECTIONS), out)[0]` und **nichts sonst** (Roast #10). Heute doppelt in `cmd_up`
  (250–251) und `_ensure_infra` (376–377). Helfer gibt `bad` zurück; **Caller entscheidet**
  weiter (cmd_up → `return EXIT_DOCTOR`; `_ensure_infra` → `raise CliError`). **Nicht** in den
  Helfer ziehen: `_auto_sync_if_needed` (in cmd_up *vor*, in `_ensure_infra` *nach* dem
  Preflight — unterschiedliche Reihenfolge!), `out.head` (nur cmd_up), trailing `print()` (nur
  cmd_up). Würde ein Implementierer die „für Symmetrie" reinziehen, ändert sich die Reihenfolge.
- `logs`/`audit`: das gemeinsame `-f/--tail`-Tail liegt nach dem Move ohnehin in `observe.py`
  neben `_tail_audit`; `cmd_logs --audit` ruft weiter exakt `_tail_audit` (kein Dispatch-/
  Verhaltenswechsel — konsistent mit Phase-1-§5a). Reine Code-Nähe, keine Logikänderung.

## 3.4 Test-Kopplung — das Refactor-Risiko (für den Roast wichtig)

Bestehende Tests **monkeypatchen Namen auf dem `cli`-Modul** und rufen Handler direkt:

- `test_up_profile.py`: `monkeypatch.setattr(cli, "compose_run", …)`, ebenso `cli.run_doctor`,
  `cli.assert_real_dirs`, `cli.assert_invariants`, `cli._wait_healthy`,
  `cli.auth.write_auth_fragment`; ruft `cli.cmd_up(...)`.
- `test_run.py`: `cli._oneoff_args(...)`. `test_audit.py`: `cli._UdsProxy`.
  `test_sync_entry.py`: `cli.subprocess`. `test_catraz.py`/`…`: `cli.Out`, `cli._ensure_gitignore`,
  `cli._run_sync`.

**Falle:** Zieht `cmd_up` nach `stack.py`, referenziert es `stack.compose_run` (sein eigener
Import). Ein `monkeypatch.setattr(cli, "compose_run", …)` greift dann **nicht** mehr →
Test bricht oder wird stumm wirkungslos.

**Vollständige Inventur** (`grep -rn "cli\." tests/cli/` ist die Quelle der Wahrheit — pro
Name: reicht Re-Export, oder muss der Test aufs neue Modul zeigen?):

| Test nutzt | Art | Mitigation |
| ---------- | --- | ---------- |
| `cli.Out` (test_catraz, test_up_profile) | Import + Konstruktion | Re-Export `from catraz.ui import Out` |
| `cli.CliError` (test_catraz:14) | Import | **Re-Export** `from catraz.errors import CliError` — cli.py muss ihn behalten (Roast #3) |
| `cli._ensure_gitignore`, `cli._run_sync` (test_catraz, test_gitignore, test_sync_entry) | Import/Aufruf | Re-Export aus `setup` |
| `cli._oneoff_args` (test_run) | Aufruf (rein) | Re-Export aus `run` |
| `cli._UdsProxy` (test_audit) | Vererbung | Re-Export aus `observe` |
| `cli.cmd_up` (test_up_profile:43,51) | Aufruf | Re-Export aus `stack` **+** Test repointen (s. u.) |
| `cli.compose_run`/`cli.run_doctor`/`cli.assert_real_dirs`/`cli.assert_invariants`/`cli._wait_healthy` (test_up_profile:17–26) | **Dependency-monkeypatch** | **Test auf `stack.*` umstellen** — Re-Export rettet das NICHT |
| `cli.auth` (test_up_profile:20 patcht `cli.auth.write_auth_fragment`) | monkeypatch am Submodul | `auth` ist ein Modul-Singleton; Patch greift, sofern `stack.py` `from catraz import auth` macht → Test auf `stack.auth` zeigen lassen (sauberer) |
| `cli.subprocess` (test_sync_entry:19 patcht, ruft `cli._run_sync`) | **Dependency-monkeypatch** | **Test auf `setup.subprocess` umstellen** (Roast #4) — nicht dem Zufall überlassen, dass cli.py `import subprocess` behält |
| `image.resolve_base` (B2, test_up_profile) | monkeypatch am `image`-Modul | Patch am `image`-Singleton — überlebt den Move, da `stack`/`run` `from catraz import image` nutzen |

**Regel:** Re-Export rettet `import` und reine Aufrufe (`cmd_up`, `_oneoff_args`, `_UdsProxy`,
`Out`, `CliError`, `_ensure_gitignore`, `_run_sync`). Wer eine *Abhängigkeit* `monkeypatch`t,
muss auf das Modul zeigen, in dem die Funktion **lebt** — betrifft `test_up_profile.py`
(→ `stack.*`) und `test_sync_entry.py` (→ `setup.subprocess`). Beide im selben Commit anpassen.

**Zyklus-Invariante (Roast #11, verbindlich):** **Kein `commands/*`-Modul importiert je aus
`catraz.cli`.** `cli` re-exportiert *aus* `commands/*` (Einbahn). `Out` wandert nach `ui.py`,
genau damit `commands/*` `Out`/`errors`/`compose` ziehen können, ohne über `cli` zu laufen.
Ein einziges `from catraz.cli import X` in einem Command reißt den Zyklus auf — verboten.

## 3.5 Reihenfolge Phase 3 (ein Commit, strikt verhaltensneutral)

1. `Out` → `ui.py`; `cli` re-exportiert `Out`.
2. `commands/`-Paket anlegen, Handler thematisch verschieben (inkl. neuer `_build_env` aus B2).
3. Dedup-Helfer (`_rc`, `_security_preflight`).
4. `main()` auf `HANDLERS`-Tabelle umstellen; doppelten `try/except` entfernen.
5. Re-Exports in `cli.py`; `test_up_profile.py` auf `stack.*` umhängen.
6. Volle Suite grün: `uv run --with pytest python -m pytest tests/ -q`.

Commits: `fix(cli): make --dry-run side-effect free`, `fix(cli): always resolve BASE_IMAGE
before a build`, `feat(cli): status exits non-zero when unhealthy`, dann `refactor(cli):
split handlers into commands/ submodules`. Keine Trailer.
