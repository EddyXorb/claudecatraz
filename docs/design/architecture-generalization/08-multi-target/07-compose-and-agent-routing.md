# 07 — Compose, git-Routing & Agent-Rendering

**Leitet ab aus** [`../08-multi-target.md`](../08-multi-target.md) §1.1, §1.2, §4.1. Lies
diese zuerst. **Hängt ab von** Schritt 01–06.

## Ziel

Die Container-/Compose-Schicht (`src/catraz/…`) so verdrahten, dass mehrere kanonische
Hosts real erreichbar sind: DNS-Aliase auf den Warden, git-`insteadOf`-Schema-Rewrite pro
Host, gerenderte REST-Basis pro Host, `no_proxy` je Host, und die neuen gruppierten
Secret-Mounts.

## Umsetzung

1. **DNS-Aliase (`assets/compose/docker-compose.yml`).** Jeden Endpoint-Host über
   `extra_hosts`/Netzwerk-Alias auf den Warden-Container zeigen lassen (heute nur der eine
   `gitlab-warden`-Name). Quelle der Host-Liste: `warden.toml`-Endpoints; die CLI, die
   compose rendert/aufruft (`src/catraz/…`, `compose.run`), leitet sie ein.
2. **git-Routing pro Host (`assets/container/git_routing.py`).** `configure_git_warden`
   von „einen `GITLAB_URL` auf `gitlab-warden` umschreiben" auf **pro Host ein
   Schema-Rewrite** heben: `https://<host>/ → http://<host>:8080/` (Hostname bleibt, §1.1),
   für jeden Endpoint-Host. Den `GITLAB_MODE=off`-Sonderzweig entfernen (off = keine
   Endpoints).
3. **REST-Basis pro Host (`assets/agents/claude/adapter.py::render_instructions`).**
   `__FORGE_REST_BASE__` von *einer* Basis auf die generische Per-Host-Regel heben (§1.2):
   „für Host X → `http://X:8080/api/v4`". Der Warden-Name darf nicht in den Instruktionen
   auftauchen.
4. **`no_proxy` (`docker-compose.yml`).** Jeden gerouteten Host zu `no_proxy`/`NO_PROXY`
   hinzufügen (heute nur `gitlab-warden`), sonst landen REST-Calls im Forward-Proxy statt
   direkt beim Warden (§1.2).
5. **Secret-Mounts (`docker-compose.yml`).** Die zwei `secrets:`-Blöcke auf
   `read_tokens`/`write_tokens` umstellen und als `READ_TOKENS_FILE`/`WRITE_TOKENS_FILE`
   reichen. Die alten `gitlab_read_token`/`gitlab_write_token`-Mounts und die
   Policy-Env-Overrides (`WARDEN_ALLOWED_PROJECTS` etc.) sowie `GITLAB_MODE`/`GITLAB_URL`
   aus dem compose-`environment` entfernen (Gegenstück zu Schritt 05).
6. **`.env`-/`warden.toml`-Assets.** `assets/config/warden.toml` auf `[git.rules]` +
   `[[git.endpoint]]` umstellen; aus dem `.env`-Template die entfernten Policy-Vars
   streichen (nur Infra-Knöpfe bleiben, §3.5).

## Nicht tun

- **Kein** `insteadOf`-Pfad-Präfix-Trick — Schema-Rewrite, Hostname bleibt kanonisch (§7
  im Hauptdokument).
- Den Warden-Hostnamen **nicht** in Remotes oder gerenderte Instruktionen leaken.
- Keinen zweiten Warden-Container pro Host.

## Tests

`tests/cli/`: `git_routing` erzeugt pro Host einen korrekten Schema-Rewrite (kein
Pfad-Trick, Hostname erhalten); `render_instructions` schreibt die Per-Host-REST-Regel ohne
Warden-Namen; compose-Rendering enthält alle Endpoint-Hosts in DNS-Alias **und** `no_proxy`.
(Echte Zwei-Host-Erreichbarkeit prüft Schritt 08.)

## Verifikation

`uv run --with pytest python -m pytest tests/cli/ tests/container/ -q && uv run mypy`

## Commit

Als `EddyXorb`, kein Co-Author-Trailer. Nachricht:

```
feat(compose): multi-host DNS aliases + git routing + rendering
```

## Fertig-Kriterium

DNS-Alias, `insteadOf`-Schema-Rewrite, gerenderte REST-Basis und `no_proxy` decken alle
Endpoint-Hosts ab; die Secret-Mounts sind `read_tokens`/`write_tokens`; alte
Policy-Env/`GITLAB_*` aus compose/`.env` entfernt; `warden.toml`-Asset im neuen Schema;
Tests grün.

## Status

✅ Erledigt. Ein paar nicht-offensichtliche Entscheidungen, die vom wörtlichen Text
abweichen bzw. ihn konkretisieren:

- **Generische REST-Regel statt Host-Enumeration.** §1.2 verlangt "eine generische Regel
  pro Host, ohne Warden-Namen zu leaken" — umgesetzt als **ein einziger, literaler
  Platzhalter-String** `http://<host>:8080/api/v4` (`WARDEN_REST_URL` in
  `docker-compose.yml`, Fallback in `entrypoint.py::_instruction_context`), den
  `render_instructions` unverändert per `str.replace` einsetzt. `<host>` ist wörtlich zu
  lesen — der Agent ersetzt es selbst durch den Host seines jeweiligen git-Remotes. Das
  spart jede Host-Enumeration beim Rendern (die Regel ändert sich nie, egal wie viele
  Endpoints konfiguriert sind) und hält `adapter.py` komplett unverändert; nur der
  Text in `AGENT.md.tmpl` erklärt die Substitution. `agent_contract.py` dokumentiert das
  jetzt explizit am `forge_rest_base`-Feld.
- **git-Routing liest `warden.toml` selbst, kein neuer Env-Pfad.** `git_routing.py` parst
  die Host-Liste direkt aus dem ohnehin schon gemounteten `/etc/catraz/warden.toml`
  (derselbe Mount, den `_instruction_context` für die Policy-Anzeige nutzt) statt über
  eine neue Env-Variable — ein Vertragsbruch weniger zwischen Compose und Container.
  `configure_git_warden()` nimmt optional einen Pfad entgegen (Tests), Default ist der
  reale Container-Pfad.
- **DNS-Alias + `no_proxy` via generierte Compose-Fragment-Datei, nicht Env-Interpolation.**
  Da Compose selbst keine variable-lange Host-Liste per `${VAR}`-Interpolation abbilden
  kann, rendert `catraz.compose.write_hosts_fragment()` bei jedem `up`/`run`/`shell`/`down`
  (`generate_resolved`, render=True-Pfad) eine kleine `.catraz/compose.hosts.yml` aus den
  `[[git.endpoint]]`-Hosts — Alias auf `gitlab-warden`s `agent-net`-Mitgliedschaft +
  `no_proxy`/`NO_PROXY` fürs Agent-Environment — und hängt sie als zusätzliche `-f`-Schicht
  an `_source_cmd` an (existenzgeprüft wie das schon vorhandene
  `compose.override.yml`, davor einsortiert, damit der User-Override weiter das letzte
  Wort hat). `_source_cmd` selbst bleibt seiteneffektfrei (schreibt nichts) — das erhält
  die dokumentierte "render=False: keine Seiteneffekte"-Garantie von `prepare()` für die
  reinen Status/Logs-Pfade. **Nicht durch einen echten Compose-Lauf verifiziert** (kein
  Docker in dieser Sandbox) — die Annahme ist, dass Compose eine Kurzform-`networks:
  [agent-net, …]` im Basis-File und eine Langform `networks: {agent-net: {aliases:
  [...]}}` im Override-File pro Netzwerkname deep-merged (dokumentiertes Compose-Verhalten).
  Echte Erreichbarkeit prüft wie geplant Schritt 08.
- **`warden.toml`-Asset: Kommentar korrigiert, Top-Level-Keys bewusst nicht angetastet.**
  Der alte Kommentar ("additive preview, not yet enforced by any guard") war seit
  Schritt 02–04 schlicht falsch (die Warden-Guards lesen `[[git.endpoint]]` längst) und
  wurde entsprechend korrigiert. Die Top-Level-Legacy-Keys (`branch_prefixes`,
  `allowed_projects`, `max_open_mrs`, …) blieben aktiv/unkommentiert stehen: der Wizard
  (`commands/setup/_wizard_*.py`) schreibt weiterhin dorthin, und
  `warden/warden/core/config_load.py` liest sie nach wie vor als eigenständigen,
  domänenweiten Fallback (nicht Teil der `[git.rules]`/`[[git.endpoint]]`-Kaskade) — sie
  vollständig zu entfernen hätte den Wizard kaputt gemacht, was explizit außerhalb des
  Scopes dieses Schritts liegt (Schritt 06s eigener Status-Abschnitt hat die
  Wizard/`doctor.py`-Migration bewusst auf "später" verschoben, nicht auf diesen Schritt).
- **`doctor.py`/Wizard bewusst unverändert.** `GITLAB_MODE`/`GITLAB_URL` werden aus der
  `.env`-Vorlage entfernt (§3.5), aber der Wizard schreibt sie weiterhin aktiv in die
  echte `.catraz/.env` (unverändertes Verhalten, `envfile.set_env_values` hängt fehlende
  Keys ans Dateiende an — keine Testbrüche). `doctor.check_gitlab`/`_gitlab_mode` bleiben
  ebenfalls unangetastet. Das ist dieselbe additive Migrationslogik wie in Schritt 06:
  compose/`.env`-Vorlage sind jetzt auf dem neuen Schema, der Wizard-Cutover ist explizit
  nicht Teil dieses Schritts.
