# Roast-Iteration 1 — Kritik & Antworten

Gegenstand: [`05-repackaging.md`](./05-repackaging.md) (Erstfassung). Ein adversarialer
Subagent („senior infra/security architect") hat die Erstfassung gegen die vier
Kernwerte **Sicherheit > Einfachheit > Transparenz > Anwenderfreundlichkeit** geroastet.
Unten je Befund: das Urteil des Roasters, **meine Antwort** (angenommen / zurückgewiesen /
teils), und die **resultierende Änderung** im Design.

Bewertung gesamt des Roasters: *Kern-Architektur ist tragfähig; die zentrale
Sicherheitsbehauptung (§4.3 „tmpfs überdeckt `.catraz`") war überzogen und teils falsch.*
Das deckt sich mit meiner Einschätzung — die Erstfassung verkaufte eine Container-lokale
Eigenschaft als globale. Die größte Einzeländerung ist darum die Umwandlung von §4.3 von
**Behauptung in eine verifizierte Spezifikation** (§4.5).

---

## #1 — Shadow-Mount verbirgt `.catraz` nicht global; Warden re-mountet die Secrets · BLOCKER · Sicherheit

**Roaster:** §4.3 argumentiert nur über den Agent, aber §4.1 verschiebt `config/state/logs/`
*in* `.catraz/`, und Warden/Proxy mounten diese weiter. „tmpfs verbirgt `.catraz`" ist nur
eine Eigenschaft *eines* Containers, wird aber als globale Eigenschaft verkauft.

**Antwort: angenommen.** Der Befund ist korrekt — es ist eine Klarheits-/Framing-Lücke,
kein Loch (der Warden *ist* die Trust-Boundary und hält die Tokens ohnehin). Aber unbenannt
verleitet er einen Implementierer dazu, dem Agenten später einen Mount zu geben, den er
nicht haben darf.

**Änderung:** Neuer Abschnitt §4.3 „Reichweite der Aussage" mit einer Mount-Tabelle (Agent
vs. Warden vs. Proxy, je Pfad/Modus/Begründung) und der von `doctor` erzwungenen Invariante:
*kein* Warden/Proxy-`.catraz`-Pfad ist zugleich agent-erreichbar.

## #2 — `.catraz/claude` ist RW in den Agenten gemountet → Credential-Vergiftung · MAJOR · Sicherheit

**Roaster:** Die Tabellenzeile „Agent schreibt nie ins Host-`.catraz`" ist **falsch**: über
`/home/dev/.claude` (RW-Bind) kann er die Host-Credential überschreiben und Hooks
persistieren, die den *nächsten* Lauf vergiften.

**Antwort: angenommen — das war ein echter Fehler.** Der Persistenz-Vektor (bösartige
`settings.json`/Hooks) ist real.

**Änderung:** Claude-Home neu modelliert (§4.3): `.credentials.json` **read-only** einzeln
gemountet (lesen ja, überschreiben nein); der **Rest** des Homes ist ein **tmpfs**
(flüchtig), das der entrypoint je Start aus image-gebackenen Quellen neu befüllt; der
entrypoint überschreibt `CLAUDE.md`/`settings.json` **bei jedem Start** (heute „falls
fehlend"). Ergebnis: kein Persistenz-Pfad von einem Lauf in den nächsten. Die korrigierte
Threat-Tabelle benennt die Ausnahme explizit.

## #3 — `/proc/self/mountinfo` leakt Host-Pfade · MAJOR · Sicherheit/Transparenz

**Roaster:** Inhalt ist verborgen, **Topologie** nicht: mountinfo zeigt tmpfs-Mountpoint und
Bind-Quellpfad des Claude-Homes — verrät, *wo* auf dem Host die Secrets liegen.

**Antwort: angenommen (als bewusst akzeptiertes Restrisiko).** Topologie ≠ Reichweite: der
Agent kann auf einen Pfad, den er nicht erreicht, nicht handeln, und einen Exfil-Kanal hat
er über Claude ohnehin (irreduzibel, README §2.1). Aber es gehört benannt, nicht impliziert
unsichtbar.

**Änderung:** Threat-Tabelle ergänzt; red-team T8 verlangt „kein *erreichbarer*
Secret-Pfad in mountinfo".

## #4 — tmpfs-über-Subpath: Ordering & `size` behauptet, nicht verifiziert · MAJOR · Sicherheit/Einfachheit

**Roaster:** Die ganze R7-Garantie ruht auf Mount-Ordering; Kurzform-`tmpfs:` hat
historisch Ordering-Quirks; fehlender Mountpoint bei Recreate-Race undefiniert.

**Antwort: angenommen.** Zu tragend, um es zu behaupten.

**Änderung:** (a) **Langform** `type: tmpfs` festgenagelt (deterministische Auflösung nach
Pfadtiefe) statt Kurzform; (b) `catraz up` stellt Existenz von `${PROJECT_DIR}/.catraz` vor
Start sicher → kein „Mountpoint fehlt"-Fall; (c) **§4.5 Verifikations-Spec** macht die
red-team-Suite zur *Definition* von „funktioniert", über CI-Matrix der Docker-Versionen.

## #5 — Symlinks nur in einer Zeile abgetan · MAJOR · Sicherheit

**Roaster:** Symlink in `.catraz/claude` (RW persistiert) `-> /` gäbe nächsten Lauf einen
Host-Escape; Symlink in `/workspace` auf das Credential.

**Antwort: teils zurückgewiesen, teils angenommen.** **Zurückweisung:** Ein Symlink *innerhalb*
eines gemounteten Verzeichnisses löst im **Container**-Mount-Namespace auf — `evil -> /`
zeigt auf Container-`/`, nicht Host-`/`. Bind-Mounts folgen Symlinks *im Quellpfad des
Mounts selbst* auf, nicht Symlinks *im Inhalt* — die Roaster-Eskalation „Host-Escape via
Symlink im Inhalt" greift daher nicht. **Annahme:** Der reale Resteffekt (Symlink aus
`/workspace` auf das eigene, ohnehin in-process gehaltene Credential) gehört explizit
geprüft, nicht erschlossen.

**Änderung:** Neuer Absatz „Symlinks lösen sich im Container-Namespace auf"; red-team T7
schreibt die Fälle fest.

## #6 — Recreate-Race korrumpiert Warden-SQLite-WAL / Quota fail-open · MAJOR · Sicherheit/Anwender

**Roaster:** `up --build` rekreiert, während der Warden in WAL/JSONL schreibt → korrupte
Quota-DB, die fail-*open* eine Sicherheitskontrolle (R5) aushebelt.

**Antwort: angenommen.**

**Änderung:** §7.2 „Recreate-/Update-Semantik": Graceful `SIGTERM` (WAL crash-konsistent,
checkpointet bei sauberem Stop); Warden-Invariante **fail-closed bei unlesbarem State**;
`up` ohne `--build` idempotent statt stillem Recreate. red-team T9 prüft die Konsistenz.

## #7 — „base-agnostischer Claude-Layer" ist teils Hand-Waving · MAJOR · Einfachheit

**Roaster:** `install-node.sh` existiert nicht; Nodes Binaries sind glibc (Alpine-Claim
falsch); `useradd || adduser` verdeckt Flag-Divergenz und regressiert den heutigen
`userdel ubuntu`-Guard (Dockerfile:62).

**Antwort: angenommen — bestes Einzelargument des Roasts.** Die Erstfassung pflegte ein
leeres Versprechen.

**Änderung:** §5.4 **ehrlich verengt** auf den **Base-Vertrag „Debian/Ubuntu + glibc +
python3"** (deckt ~jedes reale Dev-Image). Claude-Layer bleibt einfach: apt-nodesource wie
heute, **`userdel ubuntu`-Guard portiert**, statisches `gosu`. Kein erfundenes
`install-node.sh`, keine `||`-Theater-Fallbacks. `doctor base` prüft den Vertrag laut.

## #8 — Zwei-Phasen-Build = Netto-Einfachheits-*Verlust*; leichtere Alternative · MAJOR · Einfachheit

**Roaster:** Cache-Invalidierung undefiniert, Zwei-Phasen-Fehler, drei Code-Pfade. Eine
publizierte `claude-base`, die der Nutzer `FROM`t, käme mit *einem* Build aus — warum nicht?

**Antwort: teils angenommen, teils begründet zurückgewiesen.** Begründung gegen B: Bei B ist
die **Nutzer**-Schicht die *letzte* — ein vergessenes `USER root` hebt die Härtung auf;
TODO 2 verlangt aber ausdrücklich „die Sicherheit, dass das richtig gemacht wird". Bei A
(Claude-Layer OBEN, `FROM ${BASE}`) besitzt catraz die *letzte* Schicht → secure-by-
construction. Auf der Kern-Achse **Sicherheit** ist A klar besser, darum bleibt A primär.

**Änderung:** §5.2 definiert das **content-adressierte Cache-/Tag-Schema**
(`catraz-base:<sha256-12>`) und die Phasen-getrennte Fehlermeldung; **§5.5** stellt A vs. B
als Tabelle gegenüber und bietet **B als dokumentierten, gleichwertig einfacheren Pfad** für
Nutzer, die bewusst Ein-Phasen wollen — mit `doctor`-Invarianten als Netz. (Das erfüllt die
Aufgabenstellung „mehrere gleichwertige Lösungen sind ok, wenn keine klar besser ist" —
hier *ist* A auf der Sicherheits-Achse besser, also bleibt A primär, B koexistiert.)

## #9 — Build-Kontexte in `site-packages`; `warden/`+`forward-proxy/` fehlen im Wheel · MAJOR · Einfachheit/Anwender

**Roaster:** venv-Layout ist keine stabile API; zip-Installs haben keinen FS-Pfad; und §3.1
listet nur `assets`+`container` als Wheel-Includes → **Warden/Proxy bauen nach Install
nicht**. „ggf. nach Tempdir extrahieren" ist zu schwach.

**Antwort: angenommen — konkreter Packaging-Bug.**

**Änderung:** §3.1: `warden/`+`forward-proxy/` als `force-include` ergänzt; **deterministische**
Extraktion nach `~/.cache/catraz/<version>/` (nicht „ggf."), alle `build.context` zeigen
dorthin. §4.4 entsprechend („Asset-Cache, nicht venv, nicht `--project-directory`").

## #10 — Tool-Install versteckt Compose/Trust-Boundary; `--print` reicht nicht · MINOR · Transparenz

**Roaster:** Heute liest man die Compose im Klon; danach liegt sie im venv. `--print` zeigt
die Invocation, nicht den *Inhalt*.

**Antwort: angenommen.**

**Änderung:** §7.1: **`catraz show <compose|claude-layer|dockerfile|warden>`** druckt den
echten Asset-Inhalt; `init` legt **`.catraz/compose.resolved.yml`** (read-only, `docker
compose config`) als inspizierbare effektive Topologie ab.

## #11 — `find_root`-Aufwärtslauf: geschachtelte `.catraz` exponieren Geschwister · MINOR · Anwender/Sicherheit

**Roaster:** Aufwärtslauf könnte einen größeren Ahnen binden; nur `/workspace/.catraz` wird
überdeckt, sibling-`.catraz` nicht.

**Antwort: angenommen.**

**Änderung:** §4.2: geschachtelte `.catraz` **verboten**, `find_root` bricht fail-closed ab;
Mount-Root ist immer der Ordner, der das aufgelöste `.catraz` *direkt* enthält.

## #12 — `compose.override.yml` kann die Trust-Boundary auflösen; `doctor` prüft sie nicht · MINOR · Sicherheit

**Roaster:** Ein Override darf `internal: true` droppen, Token-Env/`privileged` setzen — und
es gibt keinen Check.

**Antwort: angenommen — und zur Stärke ausgebaut.**

**Änderung:** §4.4: `doctor` prüft die **aufgelöste** Konfiguration (`docker compose config`)
gegen harte Invarianten (agent-net internal, kein Token-Env im Agenten, nicht privileged,
tmpfs-Shadow vorhanden, kein Warden/Proxy-Pfad im Agenten). Das macht die Grenze *prüfbar
nach Merge* — und beantwortet zugleich den Transparenz-Punkt #10.

## #13 — Migration dünn; halb fertige Migration exponiert `./claude` · MINOR · Anwender/Sicherheit

**Antwort: angenommen.** **Änderung:** §8: `migrate` verschiebt **atomar (rename)**, `up`
**verweigert fail-closed** bei Alt-Layout-Resten unter dem Projekt-Root; Präzedenz explizit
(`.catraz` gewinnt, kein stilles Vermischen).

## #14 — api_key-Modus mountet `claude` RW; `doctor` muss „keine Credential" erzwingen · NIT · Sicherheit

**Antwort: angenommen.** **Änderung:** §6.2: konkreter Enforcement-Punkt — `doctor auth`
scheitert bei `api_key` + vorhandener `.credentials.json` (und bei `subscription` +
gesetztem `ANTHROPIC_API_KEY`).

---

## Wo der Roaster das Design *bestätigt* (unverändert gelassen)

- **Auth-Modus-XOR (§6)** — „bester Abschnitt", adressiert eine code-belegte Ambiguität.
  Nur der Enforcement-Punkt (#14) ergänzt.
- **Shadow-Mount-*Instinkt* + verworfene Alternativen B/C/D** — aus *korrekten* Gründen
  verworfen; die Architektur ist richtig, nur der Beweis fehlte (jetzt §4.5).
- **Programm/Laufzeit-Split & `.catraz/`-Heim** — die natürliche Form für TODO 3/4/5/6.
- **Statisches `gosu`** — base-stabil, korrekt.
- **Warden als alleiniger Token-Halter unverändert** — es *ist* ein Packaging-Refactor, kein
  Threat-Model-Wechsel.

## Netto nach Iteration 1

Die zentrale Schwäche (Behauptung statt Beweis bei §4.3) ist behoben: der Shadow-Mount ist
jetzt über **T1–T9** *definiert*, das Claude-Home gehärtet (RO-Credential + flüchtiges
tmpfs), die Trust-Boundary nach Override **maschinell geprüft**, und der Base-Anspruch
**ehrlich verengt**. Offen für Iteration 2: ob die Mengen an `doctor`-Invarianten/red-team-
Tests die **Einfachheit** über Gebühr belasten — die nächste Runde sollte gezielt auf
*Komplexitäts-Wucherung* schauen.
