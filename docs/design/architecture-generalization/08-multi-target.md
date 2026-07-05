# 08 — Multi-Target: mehrere git-/Forge-Instanzen pro `.catraz`

> **Superseded:** the `actions` doctrine anticipated in §3.1 (linking forward
> to 09's per-endpoint action list) was carried through 09-endpoint-actions.md
> and then replaced wholesale by
> [`10-actions-rework.md`](10-actions-rework.md): the invariant REST read
> table, the `git.fetch`/`git.push` transport verbs, and the eight-action
> vocabulary no longer exist. The endpoint/host/rules-cascade doctrine on this
> page is otherwise still current.

Ein `.catraz`-Ordner bedient mehrere Upstream-Instanzen (z.B. `gitlab.com`, ein
self-hosted `my-gitlab.de`, ein privater git-Server) über **einen** Warden-Prozess,
der das Ziel am HTTP-`Host`-Header erkennt. Der Agent behält kanonische Remotes; die
erlaubten Endpoints stehen als explizite Liste in `warden.toml`, alles Ungenannte ist
default-deny. Es gibt keinen `GITLAB_URL`- und keinen `GITLAB_MODE`-Schalter mehr —
Ziele *und* Zugriffsmodus ergeben sich aus der Config bzw. den vorhandenen Tokens.

Dieses Dokument ist der vollständige, eigenständige Plan für den Schritt (im Backlog
`07-offene-verbesserungen.md` als Punkt 8 geführt). Voraussetzung: unabhängige,
transport-neutrale Guards (§07 Punkt 6) — jeder Guard hält einen austauschbaren
Transport statt einer forge-eigenen Identität.

> **Für Menschen vs. für Agents.** Dieses Dokument ist die Referenz für Menschen und
> beschreibt das *Was* und *Warum*. Implementierende Agents finden die schrittweise
> Umsetzung (das *Wie*) im Unterordner [`08-multi-target/`](08-multi-target/) — ein
> Dokument je Schritt, exakt aus den §§ hier abgeleitet. Reihenfolge und gemeinsamer
> Arbeitsablauf: [`08-multi-target/00-index.md`](08-multi-target/00-index.md).

---

## 1. Transport & Routing

Docker-DNS/Compose (`extra_hosts` bzw. Netzwerk-Aliase) zeigt jeden gelisteten
Hostnamen auf den einen Warden-Container. Der Warden liest `request.headers["host"]`,
prüft ihn gegen die Endpoint-Liste und wählt den passenden Upstream (Basis-URL +
Credentials). Ein nicht gelisteter Host wird abgelehnt (default-deny, R6). Ein Prozess
für alle Hosts — kein Container und kein Guard pro Host.

### 1.1 Kein TLS zum Warden — Schema-Rewrite (git), gerenderte Basis-URL (REST)

Der Warden serviert **Klartext-HTTP** (uvicorn auf `:8080`), kein TLS. Ein kanonischer
`https://`-Remote würde einen TLS-Handshake erwarten (SNI, gültiges Zertifikat), den
der Warden nicht bedient; und einen HTTP-`Host`-Header kann er nur lesen, wenn die
Strecke Klartext ist. Der Warden terminiert bewusst **kein** MITM-TLS. Beide Protokolle
werden daher auf Klartext-HTTP zum Warden gebracht — auf unterschiedlichem Weg, weil
git und REST unterschiedliche Rewrite-Möglichkeiten haben:

- **git (transparent).** Der `insteadOf`-Rewrite in `~/.gitconfig` (`git_routing.py`)
  schreibt pro Host **nur Schema+Port** um und behält den Hostnamen:

  ```text
  https://my-gitlab.de/  →  http://my-gitlab.de:8080/
  https://gitlab.com/    →  http://gitlab.com:8080/
  ```

  `.git/config` bleibt kanonisch (`https://my-gitlab.de`); der Rewrite lebt nur in
  `~/.gitconfig`. DNS löst `my-gitlab.de` auf den Warden auf, der Request trägt
  `Host: my-gitlab.de:8080`, und `UpstreamRouter.resolve()` normalisiert
  Case/Port/trailing-dot weg und wählt den Upstream. Der Agent kann diesen Pfad nicht
  umgehen.

- **REST (kooperativ).** Für HTTP-API-Clients (curl, glab, python-gitlab, MCP) gibt es
  keinen `insteadOf`-Hook. Die Basis-URL wird dem Agenten mitgeteilt:
  `render_instructions` (`assets/agents/claude/adapter.py`) ersetzt
  `__FORGE_REST_BASE__` in der gerenderten `CLAUDE.md` durch die http-Warden-Basis
  (`http://<host>:8080/api/v4`). Der Agent benutzt sie.

**Warum Klartext hier sicher ist.** Der Klartext-Hop Agent→Warden verlässt nie das
interne Docker-Netz `agent-net` (`internal: true`, kein Egress); warden→upstream ist
echtes HTTPS. Die Trust-Boundary *ist* der Warden.

**Warum kooperatives REST-Routing genügt.** Die Enforcement kommt aus Containment,
nicht aus dem Routing: (1) der Agent-Container hält **keinen** Forge-Token — der Warden
ist der einzige credentialisierte Pfad, ein Request an ihm vorbei ist
unauthentifiziert; (2) `agent-net` ist `internal: true`, die einzigen Auswege sind der
Forward-Proxy (Allowlist, keine Creds) und der Warden. Ein Bypass gewinnt nichts. Das
Routing macht den vorgesehenen Pfad nur ergonomisch.

### 1.2 Konsequenzen für Rendering und Compose

- **Kanonisch gilt für REST nur eingeschränkt:** git behält `.git/config` kanonisch,
  REST bekommt `http://<host>:8080/api/v4` (http + Warden-Port). Der DNS-Alias macht
  nur den Host kanonisch, nicht Schema/Port.
- **Eine generische Regel pro Host, ohne Warden-Namen zu leaken:** „Für Host X sprich
  `http://X:8080` (git) bzw. `http://X:8080/api/v4` (REST)." N Hosts, dieselbe Regel.
- **`no_proxy` muss jeden gerouteten Host enthalten** (`my-gitlab.de`, `gitlab.com`,
  …), sonst schiebt der HTTP-Client REST-Calls in den Forward-Proxy statt direkt
  (DNS→Warden) zu gehen.

## 2. Guards: ein Guard je Typ, `Host → Upstream`-Abbildung

`GitGuard` und `ApiGuard` halten eine Abbildung `Host → Upstream` und lösen pro
Request über den `Host`-Header auf; default-deny bei Miss. Keine Guard-Kopie pro Host:

- Die REST-Pfade (`/api/v4/...`) sind über Hosts identisch strukturiert
  (Forge-API-Schema). Der Katalog (Recognizer, Capabilities, Scopes) bleibt
  **host-unabhängig**; nur der physische Transport (Basis-URL + Token) variiert.
- Eine zweite Guard-Instanz pro Host würde denselben Code mit anderem Upstream und
  eigenem Katalog-Zustand duplizieren — Overhead ohne Isolationsgewinn (derselbe
  Prozess, dieselbe Trust-Boundary).

`UpstreamRouter` kapselt die Abbildung: pro gelistetem Host wird ein eigener Upstream
gebaut, `resolve(header)` normalisiert und liefert `None` bei unbekanntem Host. Ein
kernel-eigenes Gate (`host_gate`, Teil von `kernel_gates`, läuft vor `enrich`/`decide`)
prüft `intent.host` gegen die Endpoint-Liste und deny't R6 bei Miss.

## 3. Config-Schema (`warden.toml`)

### 3.1 Domäne, Endpoints, Regeln

Die Config ist nach **Domäne** (`git`, später `db`, …) gegliedert. Eine Domäne hat
zwei Kinder: `rules` (Domänen-Default-Regeln) und `endpoint` (ein Array konkreter
Ziele). Jeder Endpoint ist **genau ein Host**:

```toml
[git.rules]                          # Domänen-Defaults (gelten für alle git-Endpoints)
branch_prefixes     = ["claude/"]
max_open_branches   = 10
max_open_mrs        = 5              # greift nur, wo MRs existieren (Forge-Endpoints)
max_writes_per_hour = 60
max_push_bytes      = "50MiB"

[[git.endpoint]]
host             = "gitlab.com"
type             = "gitlab"
allowed_projects = ["acme/infra", "acme/app"]

[[git.endpoint]]
host             = "my-gitlab.de"
type             = "gitlab"
allowed_projects = ["acme/infra"]   # gleicher Pfad, anderes Repo — durch host getrennt
rules            = { max_open_mrs = 20, branch_prefixes = ["claude/", "bot/"] }

[[git.endpoint]]
host             = "personal-gitserver.it"
type             = "plain"
allowed_projects = ["me/dotfiles"]
```

**WHAT vs. HOW.** `host`, `type`, `allowed_projects` sind die Identität/der Scope des
Endpoints (welches Ziel); das optionale `rules` ist Verhalten (wie streng). Nur das
Verhalten kaskadiert (§3.3); `allowed_projects` ist immer per-Endpoint (ein
Domänen-Default-Projekt ergäbe keinen Sinn — ein Pfad ist nur relativ zu seinem Host
eindeutig). [09-endpoint-actions.md](09-endpoint-actions.md) §1.3 erweitert diese Linie
um eine dritte Kategorie, host-unabhängigen Scope (`actions`):

> **Identität** (`host`, `type`) und **host-relativer Scope** (`allowed_projects`)
> kaskadieren nicht; **host-unabhängiger Scope** (`actions`) und **Verhalten**
> (`rules`) kaskadieren — per-Key-Merge, Listen ersetzen komplett.

### 3.2 Endpoint = ein Host

Ein Endpoint bindet genau **einen** Host. Das löst die Kollision, die eine
`hosts = [...]`-Liste mit geteiltem `allowed_projects` erzeugen würde: zwei
verschiedene Repos mit zufällig gleichem Projektpfad (`acme/infra` auf `gitlab.com`
und auf `my-gitlab.de`) sind jetzt getrennte Endpoints und werden nie vermischt. Die
Config spiegelt damit das `(host, project)`-State-Keying (§5) 1:1.

- **`host` ist der Schlüssel** und muss eindeutig sein — zwei Endpoints mit demselben
  Host → `ConfigError`. Fehlermeldungen/Logs benennen den Endpoint über seinen Host,
  kein erfundener Name nötig.
- **`type` ist ein Feld** (nicht im Pfad, da alle Einträge `[[git.endpoint]]` heißen).
  Es selektiert die Guards und die Basis-URL-Ableitung:
  - `gitlab` → git-Transport + GitLab-REST, Basis `https://<host>/api/v4`.
  - `github` → git-Transport + GitHub-REST (künftiger Guard), Basis
    `https://api.github.com` bzw. Enterprise-Form.
  - `plain` → nur git-Transport, keine Forge-API, Basis `https://<host>`.
  - Unbekannter `type` → `ConfigError` mit Liste der implementierten Typen.

Die Basis-URL wird also **regelbasiert aus `host` + `type` abgeleitet**, nicht separat
konfiguriert — nur das Token (§4) ist zusätzlicher Input.

### 3.3 Regel-Kaskade

`[git.rules]` liefert die Domänen-Defaults; ein `rules = {...}` je Endpoint
überschreibt sie:

- **Per-Schlüssel-Merge:** Effektivwert für K = `endpoint.rules[K]` falls gesetzt,
  sonst `git.rules[K]`, sonst Built-in-Default. Ein Endpoint-`rules` mit einem Key
  überschreibt nur diesen; der Rest fällt weiter auf `[git.rules]` zurück.
- **Listen ersetzen, nicht anhängen:** ein Endpoint-`branch_prefixes` *ersetzt* die
  Domänen-Liste komplett — nur so kann ein Override auch verengen.
- **Stateless vs. stateful:**
  - `branch_prefixes`, `max_push_bytes` sind reine Pro-Request-Checks — Override ist
    folgenlos.
  - `max_open_branches`, `max_open_mrs`, `max_writes_per_hour` zählen. Weil sie
    überschreibbar sind, ist die **Quote per-Endpoint** (Zählung auf den Endpoint
    gescoped), kein globaler Deckel. Für Single-Endpoint ist das verhaltensgleich zu
    heute (ein Endpoint = ein Cap = die bisherige Zählung); bei Multi-Endpoint zählt
    jeder Endpoint für sich.

### 3.4 Fail-closed-Validierung

- Unbekannter `type` → `ConfigError`.
- Doppelter `host` über die Endpoints → `ConfigError`.
- Unbekannter Key in einer `rules`-Tabelle → `ConfigError` (Typo-Schutz).
- Strukturelle Fehler (kaputtes TOML, obige Fälle) **brechen den Start ab**.
  Per-Endpoint-*Credential*-Probleme brechen dagegen **nicht** ab, sondern schließen
  nur den betroffenen Endpoint (§4.2, „fail-closed-degrade").

### 3.5 Keine Policy in `.env`, keine Overrides

Eine Einstellung, eine Quelle. Policy lebt ausschließlich in `warden.toml`, Secrets
ausschließlich in `.catraz/secrets/` (§4).

- **Entfallen:** `WARDEN_ALLOWED_PROJECTS`, `WARDEN_BRANCH_PREFIX`, `WARDEN_MAX_*`
  (env-Overrides von toml-Werten), `GITLAB_MODE` (aus Token-Präsenz abgeleitet, §4.2),
  `GITLAB_URL` (Hosts kommen aus den Endpoints). Mit `GITLAB_URL` entfällt auch der
  Begriff `implicit_host`: **jeder** Host ist explizit; eine leere Endpoint-Liste ist
  echtes default-deny (nichts erreichbar), nicht „alles erlaubt".
- **In `.env` bleiben nur selten geänderte Infra-/Build-Knöpfe** (z.B.
  `CLAUDE_CODE_VERSION`, `DEV_UID`, `NODE_VERSION`, `CLAUDE_CREDENTIAL_SOURCE`,
  `AGENT_PROFILE`, `AUTH_MODE`) — keine Policy, keine Secrets.
- **Davon zu unterscheiden ist die compose-interne Verdrahtung** (`READ_TOKENS_FILE`,
  `WARDEN_REST_URL`, `ADMIN_UDS`, `no_proxy`): das ist Plumbing zwischen Containern,
  keine vom User editierte Config, und bleibt env-basiert.

## 4. Credentials & Access-Mode

### 4.1 Secret-Dateien

Zwei gruppierte, forge-agnostische Dateien, je eine pro Capability, mit flachen
`<host><whitespace><token>`-Zeilen:

```text
# .catraz/secrets/read_tokens              (mode 0600)
# <host>  <token>   — ein Token deckt git UND REST
gitlab.com            glpat-…
my-gitlab.de          glpat-…
gitlab.internal:8443  glpat-…
```

parallel `write_tokens`.

- **Separator = Whitespace, nicht `:`** (ein Host darf einen Port tragen,
  `gitlab.internal:8443`); Split am ersten Whitespace, `#`-Kommentare und Leerzeilen
  ignoriert.
- **Ein Host-Token deckt git und REST.** Ein Forge ist ein git-Server mit kanonischer
  API darüber; ein PAT mit `api`-Scope authentifiziert beides identisch. Getrennte
  Protokoll-Tokens erzeugten nur zwei zwangsweise scope-identische Tokens.
- **Compose einmalig.** Zwei `secrets:`-Blöcke (`read_tokens`/`write_tokens`) + zwei
  Env-Zeilen (`READ_TOKENS_FILE`/`WRITE_TOKENS_FILE`). Einen Host hinzufügen = eine
  Zeile in der Datei + ein `[[git.endpoint]]` — kein compose-Edit.
- **Zustellung als docker-secret** (`/run/secrets/…`), nicht als Prozess-Env — die
  Tokens erscheinen nicht in `/proc/<pid>/environ`.
- **Auflösung im Warden.** `_resolve_host_credentials` liest die gruppierten Dateien zu
  `host → token` und füttert `host_credentials`.

### 4.2 Access-Mode aus Token-Präsenz

Es gibt keinen deklarierten Mode-Schalter; der Zugriffsmodus **je Endpoint** ergibt
sich aus den vorhandenen Tokens:

| Tokens für Host X | Mode |
| --- | --- |
| keiner | **closed** (deny-all) + Warnung |
| nur read | read-only |
| read + write | read-write |
| write ohne read | **closed** + Sicherheits-Warnung |

- **Per-Endpoint fail-closed-degrade, nie fail-stop.** Ein Credential-Problem an *einem*
  Endpoint schließt genau diesen (deny-all), reißt die anderen nicht mit. Bei
  Multi-Endpoint darf ein schlechter Token nicht den Zugang zu den korrekt
  konfigurierten Endpoints blockieren.
- **Mechanik:** kein nutzbares Credential ⇒ der Router hat keinen Upstream ⇒
  `host_gate` deny't R6. „closed" ist Reuse von default-deny, kein neuer Zustand.
- **write ohne read → closed + Warnung.** Das ist eine bewusste
  **Least-Privilege-Policy**, kein technischer Zwang (ein `api`-scoped Token läse auch):
  der breite Write-Token soll nicht auf jedem Read-Request mitfließen. Die Warnung nennt
  den Fix („lege für Host X einen read-scoped Token in `read_tokens` an").
- **Emergente Vorteile:** der Mode ist per-Endpoint (`gitlab.com` read-write,
  `github.com` read-only — nur über die hinterlegten Tokens); das frühere globale
  `GITLAB_MODE=off` ist der Fall „keine Endpoints/Tokens".

## 5. State-Keying `(host, project)`

Die Quota-/Reconcile-Zustände (`agent_branches` in `guards/git/state.py::BranchState`,
`agent_mrs` in `guards/gitlab_api/state.py::MrState`) werden nach dem zusammengesetzten
Schlüssel `(host, project)` geführt. Ohne den Host-Teil würden zwei verschiedene Repos
mit gleichem Projektpfad ihre Zähler vermischen — ein stiller Korrektheitsfehler.

- `agent_branches`/`agent_mrs` tragen eine `host`-Spalte als Teil des Primärschlüssels.
- `reconcile_branches`/`reconcile_mrs` laufen pro Host und schreiben mit dem jeweiligen
  Host als Schlüsselteil.
- Die stateful Quotas zählen **per-Endpoint** (§3.3): `open_branches(host)` /
  `open_mrs(host)` filtern auf den Endpoint. Single-Endpoint verhält sich wie eine
  reine Projektpfad-Schlüsselung.
- Es gibt keinen `implicit_host` mehr — jeder Host stammt aus einem `[[git.endpoint]]`.
- Die `host`-Spalte ist Teil des Schema-Stamps (`PRAGMA user_version` in
  `core/state.py`). Der State ist pre-1.0 wegwerfbar: kein Migrationslauf, eine ältere
  DB wird fail-closed abgelehnt, der Operator löscht die Datei.

## 6. CLI (`catraz doctor` / `catraz init`)

`doctor` ist die host-seitige Validierung des Multi-Endpoint-Setups; der Warden ist die
container-seitige Durchsetzung. Beide wenden dieselben Regeln an — `doctor` in der
**freundlichen, erklärenden** Rolle, der Warden **fail-closed**. Die Regeln stehen
deshalb in diesem Dokument einmal normativ; beide Seiten werden gegen dieselben Fälle
getestet (sie liegen in getrennten Packages, `src/catraz/` vs. `warden/warden/`, und
können keinen Code teilen).

`doctor` muss:

- die gruppierten `read_tokens`/`write_tokens` parsen (`host → token`),
- gegen die `[[git.endpoint]]`-Liste kreuzprüfen und **warnen** (nie den Start
  verhindern):
  - **Token für einen nicht gelisteten Host** → Warnung (wahrscheinlich Tippfehler);
    der Warden ignoriert ihn.
  - **Gelisteter Host ohne Token** → Warnung; der Warden startet den Endpoint **closed**
    (§4.2).
  - **write ohne read** → Warnung mit der Least-Privilege-Begründung; Endpoint closed.
- jedes vorhandene Endpoint-Token proben (Erreichbarkeit/Scope), pro Host.

`init` scaffoldet die gruppierten Secret-Dateien und eine `warden.toml` mit
`[git.rules]` + `[[git.endpoint]]`-Vorlage; das gelieferte Template dokumentiert die
implementierten `type`-Werte als Kommentar.

## 7. Nicht tun

- **Kein** `insteadOf`-Pfad-Präfix-Trick (`warden:8080/my-gitlab.de/repo.git`) — er
  macht die Remotes un-kanonisch und leakt die Warden-Adresse. Schema-Rewrite statt
  Pfad-Encoding (§1.1).
- **Kein** MITM-TLS im Warden — er bleibt Klartext-HTTP hinter der internen Netz-Grenze.
- **Kein** separater Warden-Container oder Guard pro Host — ein Warden, der nach Host
  routet, genügt (§2).
- **Keine** implizite/automatisch befüllte Host-Liste — explizite Endpoints,
  default-deny für alles Ungenannte.
- **Keine** Geheimnisse in `warden.toml`; **keine** Policy/`GITLAB_MODE`/`GITLAB_URL` in
  `.env` (§3.5).
- **Keine** getrennten git-/API-Tokens pro Host — ein Token deckt beides (§4.1).
- **Kein** un-überschreibbarer globaler Quota-Deckel — die Quote ist per-Endpoint (§3.3).

## 8. Umsetzungsstand

- **Im Warden-Paket vorhanden (in einer frühen, an dieses Dokument anzugleichenden
  Form):** Host-Routing (`UpstreamRouter` + `host_gate`), per-Host-Credential-Auflösung
  und State-Keying `(host, project)`.
- **Offen:** das `warden.toml`-Schema §3 (`[git.rules]` + `[[git.endpoint]]`-Array mit
  `type`-Feld und Regel-Kaskade), das gruppierte `read_tokens`/`write_tokens`-Format
  §4.1 und die Access-Mode-Ableitung §4.2 im Config-Loader; die `.env`-Aufräumung §3.5;
  die CLI-Schicht §6 (`doctor`/`init`); die Compose-/Rendering-Schicht (`src/catraz/…`)
  — DNS-Aliase für mehrere Hosts, `git_routing.py` für mehrere kanonische Hosts,
  Instruktions-Rendering für mehrere Remotes, `no_proxy`-Einträge; sowie ein
  Container-Test mit zwei erreichbaren Hosts und einem abgelehnten dritten.

Der Schritt gilt erst als erledigt, wenn ein Deployment über die CLI/Compose-Schicht
tatsächlich mehrere Hosts bedienen kann.
