# Roast-Iteration 2 — Kritik & Antworten

Gegenstand: [`05-repackaging.md`](./05-repackaging.md) **nach** Iteration 1. Ein zweiter
adversarialer Subagent prüfte besonders, ob die Iteration-1-Fixes in **unnötige
Komplexität** überkorrigiert haben (Gewichtung diesmal stark auf **Einfachheit**), und
drückte gezielt auf meine Rebuttals aus Runde 1.

Leitbefund des Roasters: *„Jeder Befund wurde mit **mehr** Maschinerie beantwortet, nie mit
**weniger** Oberfläche."* Das saß. Plus ein **BLOCKER** (kohärenzbrechender Widerspruch im
Claude-Home-Mount, live gegen `entrypoint.py` belegt) und der stärkste Beitrag der Runde:
eine **gleichwertige, einfachere Topologie**, die ich in Runde 0 in einer Zeile abgetan
hatte.

---

## #1 — tmpfs-Home widerspricht der entrypoint-Persistenz und sich selbst · BLOCKER · Sicherheit/Einfachheit

**Roaster:** §4.3 sagt „Rest tmpfs, aus *image-gebackenen* Quellen befüllt" — aber
`.claude.json` ist **per-User** (`organizationUuid`/`passesEligibilityCache` vom Host-`sync`,
entrypoint.py:48–53), *nicht* im Image; §6.3 sagt zugleich „Verzeichnis bleibt in beiden
Modi gemountet". Drei Abschnitte, drei Topologien. Dazu ein latenter Bug: entrypoint.py:97
`write_text` auf einen RO-gemounteten Pfad → `EROFS`.

**Antwort: angenommen — echter Widerspruch, gut getracet.**

**Änderung:** **Eine** verbindliche Home-Topologie festgezurrt (§4.3): `.credentials.json`
**und** `.claude.json` je als **RO-Einzeldatei** unter `…/.ro/` gemountet, gesamter Rest
**tmpfs**. Der entrypoint **kopiert** beide aus `.ro/` ins tmpfs-Home und patcht dann die
*Kopie* (kein `EROFS`); `CLAUDE.md`/`settings.json` werden image-baked je Start überschrieben;
`rc-debug.log` ist bewusst flüchtig. §6.3 auf dieselbe Topologie synchronisiert („kein
RW-Verzeichnis-Mount mehr").

## #2 — Die Shadow-Mount-Maschinerie löst ein selbst erzeugtes Problem; einfachere Topologie · MAJOR · Einfachheit

**Roaster:** §4.3/§4.4/§4.5/§4.2 + halbes §9 existieren nur, weil `.catraz` *im Baum* liegt
und dann wieder versteckt werden muss. Ein **Marker im Baum + State außerhalb**
(`~/.local/state/catraz/<id>`) löscht T1–T4/T7/T8, die geschachtelte-`.catraz`-Logik, zwei
Invarianten und die Ordering-Sorge — und behält die git-artige Ergonomie. Die Ein-Zeilen-
Abfuhr von Alternative B war unverhältnismäßig.

**Antwort: angenommen als gleichwertige Architektur — *nicht* als klar bessere.** Der Roaster
hat recht, dass eine reine Sicherheit+Einfachheit-Optimierung extern wählt. **Aber** TODO 6/7
verlangen *wörtlich* „nur ein `.catraz`-Ordner … und darin **alle** Hilfsdateien wie der
Claude-Ordner und die Logs" — das *ist* die In-Tree-Topologie, und ihre Anwender­freundlichkeit
(Selbst-Enthaltung, `rm -rf .catraz`, Portabilität) ist hier die **formulierte Anforderung**,
nicht Politur. Extern verliert genau diese (verwaister State, gebrochene Bindung bei Umzug,
zweiter Ort).

**Änderung:** Neues **§4.0** stellt **Option I (in-tree, Default)** und **Option II (Marker
+ extern, `--external-state`)** als **gleichwertige** Architekturen mit ehrlicher
4-Werte-Tabelle gegenüber. Per Wertehierarchie: II besser auf Sicherheit+Einfachheit, I
besser auf Anwenderfreundlichkeit *und* deckt die explizite TODO-Anforderung → **keine klar
überlegen**, beide stehen (genau der vom Auftrag erlaubte Fall). §4.1–§4.5 markieren, was
unter II *entfällt*.

> Bewusst kein Erzwingen von I: Der Auftrag erlaubt mehrere gleichwertige Lösungen, wenn
> keine klar besser ist — hier trifft das zu, weil die Achsen, auf denen sie sich
> unterscheiden, gegenläufig gewichtet sind und die TODO-Anforderung I zusätzlich stützt.

## #3 — Rebuttal #5 (Symlinks) ist bequem, nicht luftdicht · MAJOR · Sicherheit

**Roaster:** In-Container-Auflösung ja — aber der Mount-*Quellpfad* wird **host-seitig**
aufgelöst. Ist `${PROJECT_DIR}` oder `.catraz` selbst ein Symlink, bindet man unbeabsichtigt
ein anderes Host-Ziel. T7 testete nur Inhalt-Symlinks.

**Antwort: angenommen** (meine Runde-1-Abgrenzung galt nur für Inhalt-Symlinks).

**Änderung:** §4.3: `catraz up` prüft **host-seitig vor Compose**, dass `${PROJECT_DIR}` und
`${PROJECT_DIR}/.catraz` *reale* Verzeichnisse sind (`is_symlink`/`realpath`), sonst
fail-closed. Neuer Test **T7b** (host-seitiger Quellpfad-Symlink → `up` bricht ab).

## #4 — Rebuttal #8 („secure by construction") überzieht · MAJOR · Sicherheit

**Roaster:** „Letzte Schicht gewinnt" gilt für `USER`/`ENTRYPOINT`, **nicht** für setuid-
Binaries der Base, nicht für die Tatsache, dass der Claude-Layer *mit den Binaries der Base*
baut (`curl|bash`, `apt`). Die Base ist in A *und* B vertraut; A schützt nur vor
*versehentlichem* Nicht-Härten.

**Antwort: angenommen — ich habe Schutz-vor-Versehen als Lieferketten-Schutz verkauft.**

**Änderung:** §5.5 neu gerahmt (ehrliche Tabelle: A schützt vor *versehentlicher*, **keiner**
vor *feindlicher* Base); `doctor base` (§5.4) scannt nun zusätzlich **setuid/setgid** +
**non-root finalen USER** im aufgelösten Image.

## #5 — `compose.resolved.yml` ist stale-by-construction · MAJOR · Transparenz/Einfachheit

**Roaster:** `init` schreibt es, bevor `.env`/Override final sind → es lügt sofort; und es
dupliziert `doctor`s Live-`docker compose config`.

**Antwort: angenommen.** **Änderung:** Persistente Datei **gestrichen**; stattdessen
**`catraz show resolved`** läuft live, **geteilter Code-Pfad** mit dem §4.4-Invariantencheck.

## #6 — `show`-Taxonomie zu breit; Cache/prune/migrate aber berechtigt · MINOR · Einfachheit

**Antwort: angenommen.** **Änderung:** `show` auf **`compose`/`resolved`** reduziert; Rest
über dokumentierten `catraz cache-dir`. Asset-Cache, `prune`, `migrate` bleiben (vom Roaster
ausdrücklich als berechtigt bestätigt).

## #7 — Invariantenparser-Schema-Fragilität untermitigert · MINOR · Einfachheit

**Antwort: angenommen.** **Änderung:** §4.4: Parser prüft **aufgelöstes JSON**
(`--format json`), abgesichert per known-good/known-bad-Override in **derselben** CI wie
T1–T9 — kein zweiter Versionszweig.

## #8 — T1–T9 *über eine Versions-Matrix* ist Gold-Plating · MINOR · Einfachheit

**Roaster:** Einzel-Operator-Werkzeug auf *einer* Docker-Installation; die Matrix ist
Enterprise-CI. Tests behalten, Matrix streichen.

**Antwort: angenommen — die beste Einfachheits-Rückgewinnung der Runde.**

**Änderung:** §4.5: **eine gepinnte** Mindest-Docker-/Compose-Version, `doctor docker`
**verweigert darunter** den Start. „Getestet auf X, startet nicht unter X" statt Kreuzprodukt.

## #9 — Kohärenz: Querverweise/Nummerierung · MINOR · Transparenz

**Antwort: angenommen.** **Änderung:** bare „§7" → §7.1/§7.2 disambiguiert; §6.3 vs. §4.3
auf eine Home-Topologie synchronisiert (s. #1); §9-Risikotabelle auf den Iteration-2-Stand
gezogen.

## #10 — TODO-Abdeckung: `cmd_sync` ist heute Quell-blind · MINOR · Anwenderfreundlichkeit

**Roaster:** `CLAUDE_CREDENTIAL_SOURCE` ist die richtige TODO-1-Lesart, aber `cmd_sync`
(entrypoint.py:29) kodiert die Quelle hart; der 04-cli-`--from`-Flag läuft ins Leere — die
Umsetzung muss `cmd_sync` einen echten Parameter geben, nicht nur umbenennen.

**Antwort: angenommen.** **Änderung:** §6.3 benennt das explizit als Umsetzungsauflage
(„echter Quell-Parameter, nicht nur Rename"). TODO 7 ist laut Roaster *über*-bedient — die
gestrichene Matrix/Stale-Datei nimmt genau diesen Überschuss zurück.

---

## Was der Roaster als GUT bestätigt (nicht mehr anfassen)

- **§6 Auth-Modus-XOR** — code-fundiert, fail-closed; #14-Enforcement-Punkt richtig dosiert.
- **§3.1 Asset-Cache + `force-include` für `warden/`+`forward-proxy/`** — korrekter Fix.
- **§5.4 ehrliche Verengung des Base-Vertrags** — „die einzige Stelle, wo der Autor
  Subtraktion statt Addition wählte; bester Edit". Genau das jetzt öfter getan.
- **§4.2 geschachtelte-`.catraz`-Guard** — korrekt *gegeben* Option I.
- **Statisches `gosu`, `userdel ubuntu`-Guard** — korrekt.
- **T1–T9 *als Tests*** — falsifizierbar schlägt behauptet; Form richtig, nur die Matrix weg.

## Netto nach Iteration 2

Der BLOCKER (Home-Topologie) ist zu **einer** kohärenten Lösung aufgelöst (RO-Einzeldateien
+ tmpfs, kopieren-dann-patchen). Die berechtigte Einfachheits-Kritik ist *durch Streichen*
beantwortet, nicht durch noch mehr Maschinerie: Versions-Matrix → eine gepinnte Version,
stale `compose.resolved.yml` → Live-Befehl, `show`-Taxonomie → zwei Ziele. Und die größte
Erkenntnis — die **gleichwertige externe Topologie** — steht nun ehrlich als §4.0 neben dem
Default, statt in einer Zeile abgetan zu werden. Offen für Iteration 3: ob §4.0 die
**Default-Wahl** sauber genug begründet (oder ob ich mich hinter „beide gleichwertig"
verstecke, wo eine Entscheidung fällig wäre), und ob nach all dem Hin und Her die
**Gesamt-Kohärenz** des Dokuments noch trägt.
