# Roast-Iteration 3 — Kritik & Antworten (Abschluss)

Gegenstand: [`05-repackaging.md`](./05-repackaging.md) **nach** Iteration 2. Dritter und
**letzter** adversarialer Subagent. Auftrag: entscheiden, ob das Design *fertig* ist —
kohärent, ehrlich, baubar — und Über-Iteration als eigenes Versagen zu erkennen.

Verdikt des Roasters: **„one must-fix decision, then ship."** Das Dokument sei zu ~90 %
baubar und *nicht* über-iteriert; die Subtraktionen aus Runde 2 hätten echt konvergiert.
Aber die in Runde 2 an Runde 3 delegierte Frage — der **Topologie-Default** — sei mit „zwei
gleichwertige Optionen" *ausgewichen* statt entschieden. Dazu zwei kleine Baubarkeits-Lücken
(Entrypoint-Umbau unspezifiziert; `.claude.json` nie provisioniert).

Diese Runde ist insofern besonders, als ich dem Roaster in **einem zentralen Punkt
begründet widerspreche** — nicht aus Sturheit, sondern weil seine Schlussrichtung auf einer
Prämisse fußt, die *ich* (nicht der Auftraggeber) gesetzt hatte.

---

## #1/#2 — „Entscheide die Topologie; und zwar zugunsten von Option II" · BLOCKER (Einfachheit/Ehrlichkeit)

**Roaster:** Die Wertordnung sei lexikografisch **Sicherheit > Einfachheit > Transparenz >
Anwenderfreundlichkeit**. Option II gewinne Sicherheit *und* Einfachheit (Prioritäten 1+2),
Option I nur Anwenderfreundlichkeit (4). Lexikografisch heißt das: **II als Default**, I als
`--in-tree`-Opt-in. „Der Nutzer hat I verlangt" sei ein Kategorienfehler — TODO 6/7 verlange
ein *UX-Ergebnis* (ein lokaler Ordner), nicht dass die Bytes physisch im Baum liegen. Zwei
Topologien zu pflegen sei selbst eine Einfachheits-Verletzung.

**Antwort: Meta angenommen, Richtung begründet zurückgewiesen.**

*Angenommen:* Das „beide gleichwertig" **war** ein Ausweichen — Runde 2 hatte das selbst als
Risiko für Runde 3 notiert. Eine Entscheidung ist fällig, und ich treffe sie.

*Zurückgewiesen — die Entscheidung fällt auf **Option I**, nicht II, aus vier Gründen:*

1. **Die strikte Lexikografie ist nicht die Vorgabe des Auftraggebers — sie ist meine.** Ich
   hatte „Sicherheit > … > Anwenderfreundlichkeit" in die *Roast-Prompts* geschrieben. Der
   Auftraggeber nannte die vier Werte **gleichrangig** („Einfachheit, Transparenz, Sicherheit,
   sowie Anwenderfreundlichkeit"). Ohne strikte Lexikografie gewinnt nicht, was auf Achse 1+2
   *knapp* vorn liegt, sondern was alle vier zusammen am besten bedient.
2. **Option II verletzt eine explizite, *zweifach* genannte Funktionsanforderung.** TODO 6:
   „darin liegen dann **alle** … wie der **Claude-Ordner und die Logs**." TODO 7: „alles im
   `.catraz`-Ordner einnistet." II schiebt genau die genannten Dateien *aus* `.catraz`. Das
   ist nicht Priorität-4-Politur — es ist die formulierte Funktion. „Der Nutzer wollte nur
   ein UX-Ergebnis" ist die *Umdeutung*, nicht der Wortlaut.
3. **II ist nicht netto einfacher** — es tauscht die Shadow-Maschinerie gegen einen externen
   State-Lifecycle (`project-id`, `gc`, `relink`), den der Roaster selbst als unscoped rügt
   (#8). Komplexität wird verschoben, nicht gelöscht.
4. **TODO 7 fragt nach genau dem, was der Shadow-Mount liefert:** „der Agent darf diesen
   Ordner nicht lesen aber alle anderen … geht das irgendwie?" — eine Bitte um *sichere
   Mechanik*, nicht um Auslagerung. Und tmpfs-über-Unterpfad ist ein Standard-Maskier-Idiom,
   kein Glücksspiel (T2 verifiziert es; §4.5 nennt den Fallback).

*Aber die berechtigte Pflege-Last-Kritik nehme ich ernst:* Option II ist **nicht** länger
eine koexistierende, gleichwertig getestete Architektur. Sie wird zur **dokumentierten
Notluke** (§4.7, ein Absatz, `--external-state`) ohne eigenen Test-/Lifecycle-Apparat
degradiert. Damit: *eine* getragene Topologie, explizite Anforderung erfüllt,
Zwei-Pfad-Last vom Tisch.

**Änderung:** §4.0 komplett neu — von „zwei gleichwertige" zu **„Entscheidung: Option I,
mit expliziter Widerlegung des II-Vorschlags"**; §4.7 neu (Notluke II); §9/§10 entsprechend.

> Bezug zur Auftragsregel „mehrere gleichwertige Lösungen nur, wenn keine klar besser ist":
> Für die Topologie ist nach ehrlicher Prüfung **eine besser** (I erfüllt die explizite
> Anforderung, II nicht) → eine Entscheidung, kein Nebeneinander. Beim Image-A/B-Split (§5.5)
> bleibt A Default und B *dokumentierte* Abweichung — dieselbe Logik. Nirgends bleiben zwei
> Pfade gleichberechtigt stehen, wo einer besser ist.

## #3 — `.ro/`+tmpfs erzwingt einen Entrypoint-Umbau, den das Doc nicht ausspricht · MAJOR · Baubarkeit

**Roaster:** Der Symlink-Trick (entrypoint.py:73–97) wird falsch; `.claude.json` gehört ans
Home-*Root* (`~/.claude.json`), nicht in den tmpfs-`.claude`-Ordner; die „return if
exists"-Guards müssen weg. Ein „rewrite by implication".

**Antwort: voll angenommen — präzise getracet.** **Änderung:** Neues **§6.4
„Entrypoint-Umbau & Provisionierung"** macht alle drei Punkte explizit: `.claude.json` →
Home-Root kopieren-dann-patchen (Symlink-Trick entfällt), `.credentials.json` →
`~/.claude/`, Guards auf unbedingtes Überschreiben.

## #4/#6 — Niemand provisioniert `.catraz/claude/.claude.json`; api_key-„image-baked default" existiert nicht · MAJOR · Baubarkeit

**Roaster:** Die RO-Bind-Quelle muss vor `up` existieren, aber `cmd_sync` legt `.claude.json`
nur *falls auf dem Host vorhanden* an → frische Maschine = fehlende Bind-Quelle = `up`
scheitert. Und der „image-baked default `.claude.json`" für api_key ist ein erfundenes Asset.

**Antwort: voll angenommen — die lückenschließende Beobachtung der Runde.** **Änderung:**
§6.4 + §4.1: `init`/`sync` **materialisieren `.claude.json` immer** (Host-Kopie oder
Onboarding-Default); api_key **synthetisiert** ihn inline aus dem Default, den
`ensure_claude_json` heute schon kennt — kein dangling Asset.

## #5 — Home-Topologie nicht in §4.1 synchron · MAJOR · Kohärenz

**Antwort: angenommen** (§4.3/§6.3/§9 waren synchron, §4.1 stale). **Änderung:** §4.1-Layout
nennt jetzt explizit, dass `claude/` host-seitig **beide** Dateien hält (subscription) bzw.
nur `.claude.json` (api_key), inkl. der RO-Einzeldatei-Bindung.

## #7 — setuid-Scan zielt auf die Base, nicht aufs finale Image · MINOR · Sicherheit

**Antwort: angenommen.** **Änderung:** §5.4 — setuid/`USER`-Scan läuft gegen das **finale,
zusammengesetzte Image** (Base + Claude-Layer), sonst entginge ein vom Layer installiertes
setuid-Paket.

## #8 — Notluke II angeboten, aber ihr Lifecycle deferred · MINOR · Baubarkeit

**Antwort: durch die Degradierung in #1 aufgelöst.** II ist keine „gleichwertig wählbare"
Architektur mehr, sondern eine bewusst nicht-voll-spezifizierte Notluke (§4.7); der
Lifecycle wird erst bei realem Bedarf geschlossen — kein Widerspruch mehr zwischen „angeboten"
und „unscoped".

## #9 — Dritter Mount-Pfad / T2 untested · MINOR · Einfachheit

**Antwort: angenommen.** **Änderung:** §9 — der **T2-Spike läuft zuerst**; ist er rot,
greift der Pre-Start-Mount-Fallback, die Topologie-Entscheidung (I) kippt deshalb *nicht*.
Die untested-Annahme ist damit als „zuerst zu verifizieren" markiert, nicht versteckt.

## #10 — TODO-Vollständigkeit · bestätigt

Der Roaster geht alle 7 durch und bestätigt: **alle konkret adressiert** (1 Auth-XOR + §6.4,
2 Image-Schichtung, 3 entrypoint-Asset, 4 AGENT.md-Asset, 5 uv-Install, 6 `.catraz`-Heim,
7 Shadow-Mount). TODO 1/7 trugen die Baubarkeits-Lücken aus #3/#4 — jetzt geschlossen.

## #11 — CLI-Alias-Auflösung zeigt noch auf feste Container-Namen · NIT · Kohärenz

**Antwort: angenommen.** **Änderung:** §4.4 — Alias-Auflösung (`logs`/`status`) auf
Compose-Projekt + Service-Label umstellen, nicht auf feste Namen.

---

## Was der Roaster als FERTIG/GUT bestätigt

- Home-Topologie-BLOCKER aus Runde 2 *als Design* gelöst (drei von vier Abschnitten synchron;
  §4.1 war die letzte Lücke — jetzt zu).
- Die Subtraktionen aus Runde 2 (Versionsmatrix → gepinnte Version, stale Datei → Live-Befehl,
  verengter Base-Vertrag) sind echte Konvergenz, keine Über-Iteration.
- Auth-XOR (§6), Asset-Cache (§3.1), T1–T9 als Tests, statisches `gosu`/`userdel`-Guard:
  unverändert gut.
- Explizite Anweisung: **keine Runde 4** — nicht neu litigieren.

## Ist es fertig?

**Ja — nach diesem Pass.** Der Roaster nannte „one must-fix decision + zwei
Provisionierungs-Absätze, dann ship". Beides ist erledigt: die Topologie ist **entschieden**
(Option I, mit begründeter Widerlegung der II-Empfehlung), und §6.4 schließt die zwei
Baubarkeits-Lücken. Die letzte tragende *unverifizierte* Annahme (T2-Ordering) ist als
„zuerst im Spike prüfen" markiert mit klarem Fallback — das ist der ehrliche Endzustand eines
*Plans* (kein Code ist hier zu beweisen).

**Bewusst gestoppt bei drei Iterationen.** Über-Iteration ist eigenes Versagen; die
Konvergenz ist erreicht: Runde 1 schloss echte Sicherheitslöcher, Runde 2 gewann die
Einfachheit zurück, Runde 3 erzwang die eine offene Entscheidung und die letzten
Baubarkeits-Details. Weitere Runden würden nur Geschmack umschichten.
