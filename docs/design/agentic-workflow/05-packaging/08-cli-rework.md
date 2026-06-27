# 08 — CLI-Interface-Bereinigung (flag scoping)

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
