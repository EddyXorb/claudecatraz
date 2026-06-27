# TODOs

folgendes muss noch adressiert werden:  
- der sync und api key sollten sich gegenseitig ausschließen. wenn catrazt gestartet wird sollte entweder das eine oder das andere aktiv sein (und dann auch automatisch laufen, aber das claude home verzeichnis sollte im .env angegeben werden könne nvon wo kopiert wird)
- das dockerfile ist zu stark teil des ganzen, es sollte im prinzip möglich sein beliebige dockerfiles hier laufen zu haben, einen mechanismus finden wie das geht ohne den komfort des claude-layers zu verlieren (und die sicherheit, dass das richtig gemacht wird)
- der entrypoint.py sollte nicht im root stehen, zu dominant und verwirrend für einsteiger
- die AGENT.md sollte auch irgendwie als "asset" inrgendwoe verschwinden, hat keine relevanz für enduser
- es wäre cool, wenn das tool von überall her funktioniert, also man nicht extra ein repo klonen muss an den ort wo es laufen soll, sondern man es z.b. nur einmal klont und mit uv irgendwie installiert., so dass es dann in beliebigen ordnern gestartet werden kann
- damit verbunden: statt alles im root zu haben wäre es nett es in einem .catraz ordner zu verstecken, so wie es üblich ist, d.h. wenn man ein neues repo sandboxed mit catraz soll nur ein .catraz ordner erstellt werden und darin liegen dann alle einstellungen und hilfsdateien wie der claude ordner und die logs; klarere trennung zwischen programm und laufzeitdateien erreichen. 
- ganz toll wäre es, wenn man catraz einem bestehenden ordner hinzufügen kann, so dass sich alles im .catraz ordner einnistet und dann aber der agent im container diesen ordner nicht lesen darf aber alle anderen datein/ordner im ordner, geht das irgendwie?`wäre toll es so lokal wie git zu haben, aber ein bind mount auf den aktuellen ordner der dann selber den .catraz-ordner enthält erscheint mir gleichzeitig gewagt. überlege dir was.

---

## Review-Befunde 05-packaging (von der Begleit-Review während der Umsetzung)

> Reine Beobachtungen während der schrittweisen Umsetzung von Doc 04–06. **Nicht** im
> Zuge dieses Reviews behoben (bewusst), damit die Doc-Schritte 1:1 nachvollziehbar
> bleiben. Jeder Punkt mit Begründung, warum er ein Problem ist.

### B1 — `.env.example` schleppt tote `CLAUDE_HOME`/`PROJECT_DIR`-Defaults mit (Spec-Verstoß Doc 02)

Doc 02 §2.1 fordert wörtlich: *„`CLAUDE_HOME`/`PROJECT_DIR`-Defaults (`~/.claude`,
`./workspace`) entfernen — `PROJECT_DIR` ist jetzt Pflicht (catraz setzt es)."* Aus der
**Compose**-Datei wurden sie entfernt, aber `src/catraz/assets/.env.example` endet weiterhin
mit:

```ini
CLAUDE_HOME=./claude
PROJECT_DIR=./workspace
```

**Warum ein Problem:** Nach der `.catraz`-Migration ist `PROJECT_DIR=./workspace` schlicht
falsch — `compose.run` setzt `PROJECT_DIR` zur Laufzeit auf den Projekt-Root und überschreibt
den `.env`-Wert (Prozess-Env schlägt `--env-file`). Der Eintrag ist also wirkungslos *und*
irreführend: ein Einsteiger, der ihn als „so konfiguriere ich das Projektverzeichnis" liest,
liegt komplett daneben. `CLAUDE_HOME` ist nach Doc 04 (tmpfs-Home + `CLAUDE_CREDENTIAL_SOURCE`)
ganz tot. Beide Zeilen gehören gestrichen; das war bereits in Doc 02 fällig und wurde übersehen.

### B2 — `_run_sync` findet den Entrypoint nur im Repo-Klon, nicht installiert (bricht Projektziel „von überall lauffähig")

`cli._run_sync` löst die host-seitige Sync-Tool-Datei so auf:

```python
entry = root / "src" / "catraz" / "assets" / "container" / "entrypoint.py"
```

`root` ist das **gesandboxte Projekt** (per `find_root`/`.catraz`), nicht das installierte
catraz. In einem beliebigen Projektordner existiert `<root>/src/catraz/...` nicht →
`catraz sync`, der Sync-Schritt in `catraz init` und der Auto-Sync in `catraz up` scheitern mit
„entrypoint.py not found".

**Warum ein Problem (und warum es ein Plan-Loch ist):** Doc 01 §1.3 hat den Fix ausdrücklich
vertagt — *„`cli._run_sync`: Entrypoint-Pfad auf `<repo>/src/catraz/assets/container/
entrypoint.py` setzen (in Doc 04 ohnehin überarbeitet)."* Doc 04 hat `cmd_sync`/`_run_sync`
zwar inhaltlich umgebaut (Quelle, `.claude.json`), aber den **`entry`-Pfad nie korrigiert**.
Der Übergabepunkt zwischen Doc 01 und Doc 04 ist also durchgefallen. Das Tool funktioniert nur
im dogfooding-Klon (wo `root` == catraz-Repo zufällig `src/catraz/` hat) und bricht in genau
dem Szenario, das Doc 01 verspricht (`uv tool install` + Start in fremden Ordnern, TODO-Punkt
5). Korrekt wäre `asset_root() / "assets" / "container" / "entrypoint.py"` (der Pfad, über den
alle anderen Assets bereits aufgelöst werden). Die Unit-Tests fangen das nicht, weil sie
`_run_sync` nicht über einen installierten Pfad ausüben. **Nicht hier behoben** (gehört in
einen eigenen Fix-Commit / eine Doc-Korrektur), aber blockiert das Weiterbauen von Doc 05/06
nicht.

### B3 — Doc 05 führt `ENTRYPOINT` ein, sagt aber nicht, dass `command:` aus dem Compose muss (Doppelinvokation)

Bis Doc 04 hat das Agent-Image **kein** `ENTRYPOINT`; der Start kommt über die Compose-Zeile
`command: ["python3", "/entrypoint.py"]`. Doc 05 §5.1 lässt den `claude-layer`-Dockerfile mit
`ENTRYPOINT ["python3", "/entrypoint.py"]` enden, beschreibt aber nur den `build:`-Block-Umbau
und **erwähnt das `command:` mit keinem Wort**. Bliebe es stehen, hängt Docker das `command`
als Argumente an den ENTRYPOINT → `python3 /entrypoint.py python3 /entrypoint.py`; argparse im
Entrypoint sähe `python3` als Subcommand und bräche ab.

**Warum ein Problem:** Die Doc ist an dieser Stelle nicht in sich geschlossen — wer sie 1:1
befolgt, baut einen Container, der beim Start sofort kaputtgeht. Bei der Umsetzung **habe ich
die `command:`-Zeile entfernt** (zwingend, damit der argumentlose ENTRYPOINT-Aufruf =
Daemon-Start funktioniert und Doc 06 später `… local -- <args>` als ENTRYPOINT-Argumente
durchreichen kann). Das ist der einzige Punkt in diesem Review, an dem ich vom reinen
Doc-Wortlaut abgewichen bin — bewusst, weil der Doc-Text hier lückenhaft ist. Die Doc 05 §5.1
sollte den `command:`-Wegfall explizit aufnehmen.

### B4 — Asset-Cache invalidiert nie bei gleicher Version trotz geänderter Assets (Zero-Install-Footgun)

`paths.asset_root()` extrahiert Assets nach `~/.cache/catraz/<__version__>/` und setzt einen
`.extracted`-Marker; existiert der Marker, wird **nie neu kopiert**. Der Cache-Key ist
**ausschließlich** `__version__`. Im Zero-Install-/Dev-Betrieb (Quelle = Repo, Version bleibt
`0.2.0`) bedeutet das: jede Änderung an einem Asset (Compose, Dockerfile, config) propagiert
**nicht**, bis man `~/.cache/catraz` von Hand löscht oder die Version hochzieht. Konkret in
diesem Review aufgeschlagen: der neue `test_image_assets.py` liest die echten (nicht
gemockten) Cache-Assets und sieht das alte Layout, bis der Cache geleert wird — der Testlauf
braucht ein vorgeschaltetes `rm -rf ~/.cache/catraz`.

**Warum ein Problem:** Für veröffentlichte Wheels ist es harmlos (Version steigt mit jedem
Release), aber für den im Plan ausdrücklich gewollten Zero-Install-/Entwicklungsmodus
(TODO-Punkt 5, Doc 01) ist es ein stiller Footgun: man editiert ein Asset, startet `catraz`,
und bekommt weiter das alte Verhalten — ohne jede Fehlermeldung. Sinnvoll wäre ein
Cache-Schlüssel, der im Zero-Install-Fall den Quell-Mtime/Hash einbezieht (oder im
`_repo_root()`-Zweig den Marker ignoriert und immer frisch kopiert). **Nicht behoben.**

### B5 — Doc-Test `test_tag_is_content_addressed` (Doc 05 §5.2) ist nicht lauffähig

Der wörtlich vorgegebene Mock in Doc 05 §5.2 ist:

```python
monkeypatch.setattr(image.subprocess, "run",
    lambda cmd, **k: seen.setdefault("tag", cmd[cmd.index("-t")+1]) or type("R",(),{"returncode":0})())
```

`dict.setdefault` **gibt den gerade gespeicherten Tag-String zurück** (truthy) → das `or`
schließt kurz und das Lambda liefert den **String** statt des Fake-Result-Objekts. Das
verbatim kopierte `image._build_base` macht danach `if r.returncode:` →
`AttributeError: 'str' object has no attribute 'returncode'`. Der Test kann gegen den
verbatim-`image.py`-Code also **nie** grün werden.

**Warum ein Problem:** Doc 00 erhebt „grün-testbar abgeschlossen, bevor committet wird" zur
Bedingung; ein Doc-Schritt, der einen nicht lauffähigen Test mitliefert, verletzt die eigene
Akzeptanzregel. Bei der Umsetzung wurde das Lambda durch eine äquivalente benannte `fake_run`
ersetzt (Tag merken, dann Result-Objekt zurückgeben) — Capture-Ziel und Assertion
unverändert. Reiner Doc-Fehler, in der Implementierung umgangen; die Doc-Vorlage sollte
korrigiert werden.

### B6 — `BASE_DOCKERFILE`-Build-Kontext = Dockerfile-Verzeichnis: stille `COPY`-Falle (Projektziel „beliebige Dockerfiles")

`image._build_base` baut mit `docker build -f <dockerfile> <dockerfile.parent>`, d. h. der
Build-**Kontext** ist immer das Verzeichnis der Dockerfile-Datei. Für die mitgelieferte
Default-Base ist das unkritisch (kein `COPY`, alles wird per apt/curl/pip geholt). Für ein
**benutzereigenes** `BASE_DOCKERFILE` ist es eine Falle: zeigt jemand auf
`BASE_DOCKERFILE=./docker/Dockerfile.dev` und dessen Dockerfile macht `COPY ./scripts /opt`
in Erwartung von Repo-Root-relativen Pfaden, ist der Kontext `./docker/` — `COPY` kopiert das
Falsche oder bricht ab. Es gibt keine Möglichkeit, Kontext und Dockerfile getrennt anzugeben.

**Warum ein Problem:** Genau TODO-Punkt 2 („es sollte möglich sein, beliebige Dockerfiles
laufen zu haben") wird hier nur eingeschränkt erfüllt — nämlich nur für Dockerfiles ohne
kontextrelative `COPY`/`ADD` oder solche, die ihren Kontext zufällig im selben Ordner haben.
Ein echtes „beliebiges Dockerfile" mit Build-Kontext = Projekt-Root ist nicht abbildbar.
Sinnvoll wäre ein zusätzliches `BASE_CONTEXT` (Default = Dockerfile-Verzeichnis, überschreibbar
auf z. B. `.`). **Nicht behoben** — Design-/Plan-Einschränkung, festgehalten zur Entscheidung.

### B7 — Token-Refresh im Subscription-Modus überlebt keinen Container-Neustart (RO-Quelle + tmpfs-Kopie)

Im Subscription-Modus mountet `auth.py` (`SUBSCRIPTION_FRAGMENT`) die Host-Datei
`.catraz/claude/.credentials.json` **read-only** nach `/home/dev/.claude/.ro/.credentials.json`.
Der Entrypoint (`build_home`) **kopiert** sie beim Start nach `/home/dev/.claude/.credentials.json`
— und `/home/dev/.claude` ist ein **tmpfs** (Doc 04). Claude liest/schreibt also die
beschreibbare tmpfs-Kopie, nie den RO-Mount. In-Session-Schreiben (OAuth-Token-Refresh)
funktioniert deshalb — aber die Aktualisierung bleibt im tmpfs und **propagiert nie zum Host
zurück**; sie ist beim Container-Stop verloren. Beim nächsten Start wird wieder die
ursprüngliche Host-`.credentials.json` einkopiert.

**Warum ein Problem:** Refresht Claude während der Session das Access-Token und **rotiert
Anthropic dabei das Refresh-Token** (Refresh-Token wird bei Benutzung invalidiert/ersetzt — bei
OAuth verbreitet), dann verbraucht der In-Container-Refresh das Token, der Host behält das
inzwischen tote. Ein späterer `catraz up`/`local` startet dann mit einem ungültigen
Refresh-Token → Auth-Bruch, der nur durch erneutes `catraz sync` zu beheben ist. Ohne
Rotation läuft alles unbegrenzt. Das Verhalten hängt also an einer **unverifizierten Annahme
über Anthropics Token-Lebenszyklus**. Bewusster Trade-off des RO-Designs (der Agent soll die
Host-Credential nicht überschreiben können), aber die Persistenz-Lücke gehört dokumentiert und
ggf. durch einen kontrollierten Rück-Sync (Container→Host, nur `.credentials.json`, nur im
Subscription-Modus) entschärft. **Nicht behoben** — vom Nutzer beim Lesen von `auth.py`
aufgeworfen, hier festgehalten.

### B8 — Doc 06 schreibt zwei gleichnamige Testdateien vor → erzwingt globalen pytest-Import-Modus-Wechsel

Doc 06 legt in Commit 6.2 `tests/container/test_local.py` und in Commit 6.3
`tests/cli/test_local.py` an — **identischer Basename in zwei Verzeichnissen ohne
`__init__.py`**. Unter pytests Default-Import-Modus (`prepend`) bricht das Collecting mit
„import file mismatch", weil beide Module unter demselben Top-Level-Namen `test_local`
importiert würden. Bei der Umsetzung wurde das mit `addopts = "--import-mode=importlib"` in
`pyproject.toml` gelöst (pytests kanonischer Fix für Basename-Kollisionen).

**Warum ein Problem:** Die Doc verlangt eine global wirksame Test-Konfigurationsänderung,
ohne sie zu erwähnen — wer die beiden Commits 1:1 abarbeitet, bekommt zwischen 6.2 und 6.3
plötzlich rote Collection-Fehler, deren Ursache nicht im jeweiligen Commit liegt. Der
`importlib`-Modus ist vertretbar (ganzer Baum, inkl. `tests/redteam`, kollektiert weiter sauber:
57 Tests), aber er ändert das Importverhalten für **alle** künftigen Tests. Alternativen wären
gewesen: eine der Dateien umbenennen (z. B. `test_local_cli.py`) oder `__init__.py` in
`tests/cli`/`tests/container` legen. Reiner Doc-Nit mit globaler Nebenwirkung — festgehalten,
damit die Modus-Entscheidung bewusst getroffen ist, nicht als stille Notlösung.
