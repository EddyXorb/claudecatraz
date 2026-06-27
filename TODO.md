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
