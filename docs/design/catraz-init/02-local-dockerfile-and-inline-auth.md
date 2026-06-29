# Plan: catraz-init-Rework — lokales Dockerfile, Inline-Auth, `.catraz`-Tiers, `init --from`

> Status: plan (pre-roast) · Topic: catraz-init · Ersetzt Teile von
> [01-baseimage-setup.md](01-baseimage-setup.md) · Iterationen geplant: 1

## Goal

Die `catraz`-Initialisierung soll **lokal, transparent und reproduzierbar**
werden — passend zum Sicherheitsanspruch des Tools. Vier unabhängige
Arbeitsstränge:

- **A** — Basis-Image immer als lokales `.catraz/config/image/Dockerfile`
  (Default `FROM ubuntu:24.04`); gebündelte Toolchain raus; `BASE_*` nur noch als
  `.env`-Notausgang.
- **B** — Auth-Fragment inline aus statischen Assets; kein generiertes
  `.catraz/.auth.compose.yml` mehr.
- **C** — `.catraz` in klare Tiers ordnen (`claude/` → `secrets/claude/`,
  `run/warden` → `state/warden/run/`) + ein seed-bares `.catraz/README.md`, das
  erklärt, was editierbar ist und wozu jede Datei dient.
- **D** — `catraz init --from <path>`: Entscheidungen inkl. Secrets aus einer
  bestehenden Init als bestätigbare Defaults übernehmen.

Reihenfolge nach Risiko/Aufwand: **B → C → A → D** (unabhängig mergebar).

## Entscheidungen (settled) & Kurzbegründung

- **Default-Basis = `FROM ubuntu:24.04` (+git)**; die cpp-rust-python-Toolchain
  ist anwendungsfall-spezifisch und verlässt das generische Tool. Öffentliches
  Image = maximale Transparenz, kein opaker `catraz/base`-Tag.
- **`git` UND die claude-layer-Build-Deps (`curl`, `ca-certificates`, `gnupg`)
  werden in `claude-layer` garantiert**, nicht der swap-baren Nutzerbasis
  überlassen. `git` = harte Invariante (Commits/Warden-Push); `curl`/`gnupg` =
  Build-Dep der NodeSource-Pipe (sonst `curl: command not found` auf
  `ubuntu:24.04`, das diese Tools nicht mitbringt — die heutige cpp-rust-python-
  Basis tat es, der neue Default nicht).
- **Debian/Ubuntu-Annahme** der `claude-layer` (`apt-get`, NodeSource, `gosu`)
  wird (1) im Default-Dockerfile kommentiert und (2) bei Build-Fehler als
  wahrscheinliche Ursache gemeldet.
- **`BASE_IMAGE`/`BASE_DOCKERFILE`/`BASE_CONTEXT` bleiben als `.env`-Override**
  (kein Wizard-Prompt mehr). Präzedenz unverändert:
  `BASE_IMAGE` > `BASE_DOCKERFILE` > lokales `config/image/Dockerfile`.
- **Auth-Fragment** sind statische Konstanten → als Assets ausliefern, per
  `AUTH_MODE` auswählen; nichts mehr nach `.catraz` schreiben.
- **`claude/` → `secrets/claude/`** (alle Credentials an einem 0700-Ort —
  Audit-Eigenschaft), **nicht** `state/`.
- **Leichtes Tiering, kein C2**: kein physisches `edit/inspect/internal`, sondern
  ein `.catraz/README.md`-Tierguide. ~80 % der Klarheit zu ~20 % der Kosten.
- **`init --from` erbt genau edit + secrets**, nie inspect/internal — die
  Tier-Definition liefert die Inherit-Regel.

## Verworfene Alternativen

- **C2 — physische `edit/inspect/internal`-Ordner**: dauerhafte Pfad-Tiefe
  (`.catraz/edit/.env`), breiter Änderungs-/Test-Radius, und Ordnernamen
  erzwingen nichts (Schreibschutz kommt aus Dateimodi). README statt Ordner.
- **Externes Dockerfile als Softlink in `.catraz` spiegeln**: zu undurchsichtig —
  sieht aus wie Kopie, wird editiert, ändert unbemerkt das Original; Read-only
  bei Symlinks kaum erzwingbar.
- **Auto-Cache der Init-Entscheidungen als stille Defaults**: globaler State im
  *versions-flüchtigen* Cache-Ordner (`asset_root()` rmtree't bei
  Version/Source-Änderung) → bei jedem Upgrade weg, und intransparent. Falls je
  nötig: opt-in `XDG_CONFIG_HOME/catraz/defaults.env` mit sichtbarer Meldung,
  nicht der Cache. `--from` deckt den Bedarf explizit.

---

## Workstream B — Auth-Fragment inline (zuerst)

### Kontext

`.catraz/.auth.compose.yml` existiert nur, weil `_source_cmd()` es an zwei
Stellen als `-f`-Input braucht: (1) `generate_resolved`, (2) Layered-Fallback,
wenn `config`-Rendering fehlschlägt. Die Inhalte (`SUBSCRIPTION_FRAGMENT`,
`API_KEY_FRAGMENT` in `auth.py`) sind aber **statische Konstanten** mit nur
`${PROJECT_DIR}`-Interpolation — nur die *Auswahl* per `AUTH_MODE` ist dynamisch.

### Steps

1. **Assets anlegen**: `src/catraz/assets/compose/auth.subscription.yml` und
   `…/auth.api_key.yml` mit den heutigen Fragment-Inhalten. Credential-Pfade im
   Subscription-Fragment bleiben hier **vorerst auf dem aktuellen Pfad**
   `${PROJECT_DIR}/.catraz/claude/…` — Workstream C re-pointet sie atomar
   zusammen mit `claude_home` (so bleibt `main` zwischen B- und C-Merge grün;
   B allein darf den Pfad nicht vorziehen, sonst schreibt `catraz sync` nach
   `claude/`, während die Bind-Source `secrets/claude/` läse).
2. **`auth.py`**: `write_auth_fragment()` und die beiden String-Konstanten
   entfernen; `auth_mode()` bleibt.
3. **`compose.py` (`_source_cmd`)**: statt `root/".catraz/.auth.compose.yml"`
   das mode-abhängige Asset anhängen:
   `asset_root()/"assets/compose"/f"auth.{auth_mode(root)}.yml"` (existiert
   immer → bedingungsloses `-f`).
4. **`compose.py` (`prepare`)**: den `_auth.write_auth_fragment(root)`-Aufruf
   streichen — `render=True` schreibt nur noch `compose.resolved.yml`.
5. **Aufräumen**: Doku-Referenzen auf `.auth.compose.yml` entfernen. **Nicht** in
   `.gitignore` suchen — `_ensure_gitignore` schreibt nur die Zeile `.catraz/`,
   die es ohnehin abdeckt.
6. **Tests**: `test_compose_resolved`, `test_secrets` auf die statischen Assets
   umstellen; **`test_auth.py`** (`test_fragment_subscription`/`_api_key` rufen
   das entfernte `write_auth_fragment` und prüfen `.catraz/.auth.compose.yml`)
   auf Asset-Auswahl umschreiben oder ersetzen.
7. **Hinweis Verhaltensänderung**: `_source_cmd` ruft jetzt `auth_mode(root)` auf
   jedem Compose-Aufruf (auch `compose_ps`, `assert_invariants`, default `run`).
   Ein ungültiges `AUTH_MODE` schlägt damit an mehr Stellen fehl (fail-fast,
   gewollt) — kurz dokumentieren.

---

## Workstream C — `.catraz`-Tiers + README

### Ziel-Topologie

```text
.catraz/
  README.md             ← Tierguide (seed-once Asset, s.u.)
  .env                  ← edit
  config/               ← edit (image/Dockerfile, warden.toml, squid.conf, allowlist.txt)
  secrets/              ← deine Credentials, 0700 (gitlab-tokens, anthropic-key, claude/)
  compose.resolved.yml  ← inspect (generiert)
  logs/                 ← inspect (audit)
  state/                ← internal (warden/db + warden/run-socket)
```

### Steps

1. **`doctor.py:383` (`_doctor_fix`-Verzeichnisliste)** — die einzige Stelle, die
   das Layout anlegt. Generische 0755-Loop:
   `["config", "state/warden", "logs/warden", "logs/squid", "claude", "run/warden"]`
   → `["config", "state/warden/db", "state/warden/run", "logs/warden",
   "logs/squid"]`. **`secrets/` und `secrets/claude` NICHT in diese Loop** —
   sonst legt `mkdir(parents=True)` `secrets/` mit umask-Default (0755) an und das
   spätere `secrets_dir.mkdir(mode=0o700, exist_ok=True)` ist ein No-op (mode wird
   bei existierendem Dir ignoriert). Stattdessen explizit **vor** der Loop:
   `(cat/"secrets").mkdir(mode=0o700, exist_ok=True)` + `chmod(0o700)` und
   `(cat/"secrets/claude").mkdir(mode=0o700, parents=True, exist_ok=True)` +
   `chmod(0o700)`.
2. **`doctor.py` chown-Loop (≈Zeile 397–402)**: `for d in ["state","logs","run"]`
   → `["state","logs"]`. `cat/"run"` existiert nicht mehr → `os.chown` würde
   `FileNotFoundError` (≠ `PermissionError`) werfen und `init`/`doctor --fix`
   crashen. (`state/warden/run` ist von `state` abgedeckt.)
3. **`paths.py` (`claude_home`)**: `root/.catraz/claude` →
   `root/.catraz/secrets/claude` (einziger Host-Chokepoint — sync/doctor/setup
   gehen darüber).
4. **`assets/compose/auth.subscription.yml`** (aus B atomar re-pointen):
   Credential-Quelle `${PROJECT_DIR}/.catraz/claude/…` →
   `${PROJECT_DIR}/.catraz/secrets/claude/…`.
5. **`doctor.py:331`**: hartkodiertes Hint-Literal
   `"remove .catraz/claude/.credentials.json"` → `secrets/claude/...`
   (idealerweise über `claude_home` formatiert, nicht erneut hartkodieren).
6. **`compose/docker-compose.yml`** Bind-Sources:
   - `state/warden:/var/lib/warden` → `state/warden/db:/var/lib/warden`
   - `run/warden:/run/warden` → `state/warden/run:/run/warden`
   (Containerpfade unverändert.)
7. **`observe.py:99` + `doctor.py:295`**: Socket-Hostpfad
   `.catraz/run/warden/admin.sock` → `.catraz/state/warden/run/admin.sock`.
8. **README-Asset** `src/catraz/assets/catraz-README.md` (Inhalt unten); in
   `cmd_init` nach `.catraz/README.md` seeden, falls nicht vorhanden (analog
   `_init_config_templates`).
9. **Tests**: `test_paths`, `test_doctor_*`, `test_secrets`, `test_ps`/Socket-
   bezogene Tests auf neue Pfade ziehen; ein Test, der `secrets/` == 0700 nach
   `init` prüft, ergänzen (deckt C2-Regression dauerhaft ab).

### README-Asset-Inhalt (`.catraz/README.md`)

```markdown
# .catraz — this sandbox's configuration

Everything that defines one catraz sandbox lives here, in three tiers.

## Yours — edit freely
- `.env` — build & wiring (auth mode, GitLab URL, optional `BASE_*` overrides)
- `config/image/Dockerfile` — the base image (`FROM …`); catraz layers Node +
  Claude Code on top. Must be Debian/Ubuntu-based.
- `config/warden.toml` — GitLab policy (allowed projects, limits)
- `config/squid.conf`, `config/allowlist.txt` — egress-proxy rules
- `secrets/` — your credentials (GitLab tokens, Anthropic key, Claude login).
  Mode 0700, never commit.

## Output — read, don't edit
- `compose.resolved.yml` — the complete docker-compose catraz actually runs,
  fully interpolated. Regenerated on every state-changing command; host- and
  version-specific (not portable). Read it to see exactly what runs: mounts,
  networks, and secrets (as file references, never values). Hand edits are
  overwritten.
- `logs/` — audit trail: proxy egress, warden git activity, agent transcripts.

## Internal — leave alone
- `state/` — warden database and runtime socket.
```

---

## Workstream A — Lokales Dockerfile, ubuntu-Default, `git`, Debian-Annahme

### Build-Topologie

```text
.catraz/config/image/Dockerfile  →  catraz-base:<hash>   (Nutzer-Basis, FROM ubuntu:24.04 default, editierbar)
            ↓ FROM
claude-layer/Dockerfile          →  Agent-Image          (catraz-kontrolliert: +git +Node +Claude Code)
```

`claude-layer` bleibt catraz-kontrolliert und für den Nutzer unveränderbar; der
Nutzer besitzt ausschließlich die Basis.

### Steps

1. **`assets/bases/cpp-rust-python/` entfernen** (inkl. `dockerfile-lint`-CI-
   Eintrag und Tests, die dagegen bauen).
2. **Default-Dockerfile-Asset** `src/catraz/assets/image/Dockerfile`:

   ```dockerfile
   # catraz builds the Agent runtime (Node + Claude Code) on top of this via apt-get.
   # THE BASE MUST THEREFORE BE DEBIAN/UBUNTU-BASED (apt available). Other distros
   # (Alpine/musl, RHEL/dnf) will fail the claude-layer build.
   # Change the FROM or append your own RUN/COPY lines.
   FROM ubuntu:24.04
   RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*
   ```

3. **`commands/setup/__init__.py` (`_init_config_templates`)**: zusätzlich
   `config/image/` anlegen und das Default-Dockerfile dorthin seeden, falls
   nicht vorhanden.
4. **`image.py` (`resolve_base`)**: nur den `default`-Zweig ändern —
   `_build_base(asset_root()/"assets/bases/cpp-rust-python/Dockerfile")` →
   `_build_base(root/".catraz/config/image/Dockerfile")`, Kontext = dessen
   Verzeichnis. Präzedenz `BASE_IMAGE` > `BASE_DOCKERFILE`(+`BASE_CONTEXT`)
   bleibt. **Existenz-Guard**: vor `_build_base` (das in `read_bytes()` ohne
   Prüfung liest) `df.exists()` checken und `CliError` werfen — analog zum
   `BASE_DOCKERFILE`-Zweig; sonst gibt ein gelöschtes/fehlendes lokales
   Dockerfile einen rohen `FileNotFoundError` (den `check_base` nicht fängt).
5. **`image.py` (`_build_base`)**: bei Build-Fehler `CliError`, die die
   Debian/Ubuntu-Annahme als wahrscheinliche Ursache nennt (statt nur „base
   build failed").
6. **`claude-layer/Dockerfile`**: `git curl ca-certificates gnupg` in die
   `apt-get install`-Zeile aufnehmen (Garantie unabhängig von der Nutzerbasis —
   `curl`/`gnupg` sind Build-Deps der NodeSource-Pipe, `git` ist Laufzeit-
   Invariante). Ohne `curl` bricht der Default-Build sofort.
7. **`_wizard_interactive.py`**: ggf. vorhandenen `_prompt_base_image`-Block
   entfernen; einzeiliger Hinweis „edit `.catraz/config/image/Dockerfile` to
   change the base".
8. **`_wizard_yes.py`**: `BASE_*` aus Umgebung → `.env` **beibehalten**
   (CI/Power-User-Override).
9. **`.env.example`**: `BASE_*`-Kommentare behalten, als „Power-User-Override:
   übersteuert `config/image/Dockerfile`, falls gesetzt" umformulieren.
10. **CI `dockerfile-lint`**: neuen Default-`image/Dockerfile` + `claude-layer`
    linten; cpp-rust-python-Eintrag entfernen.
11. **Tests**: `test_image`, `test_run_base_image` anpassen; **`test_image_assets.py`**
    (assertet `bases/cpp-rust-python/Dockerfile` existiert) auf das neue
    `assets/image/Dockerfile` umstellen.

### Repo-Kontext

Lokaler Default-Build hat Kontext `.catraz/config/image/`. Wer Repo-Dateien in
die Basis braucht: woanders mit beliebigem Kontext bauen + taggen
(`BASE_IMAGE=mytag`, auch lokal gebaute Tags funktionieren mit `FROM`) oder
`BASE_DOCKERFILE`+`BASE_CONTEXT` setzen.

---

## Workstream D — `catraz init --from <path>`

### Verhalten

Kein neuer Wizard-Modus: derselbe Flow, nur mit Defaults aus `--from`, die man
mit Enter bestätigt.

- **Interaktiv**: jeder Prompt zeigt den `--from`-Wert als Default; Enter
  übernimmt (überschreibt lokal Vorhandenes), Tippen ersetzt. `--from` re-promptet
  auch bereits gesetzte Werte (impliziert das „nicht-überspringen", das sonst
  `--force` gibt).
- **Scalars** (`.env`): per-Wert-Prompt mit From-Default.
- **Config-Dateien**: per-Datei-Bestätigung „von `--from` übernehmen? [Y/n]".
- **Secrets**: per-Secret-Bestätigung **ohne Anzeige** — „geerbtes Token behalten
  (Enter) / neues eingeben"; Wert wird nie geechot.
- **`-y`**: nimmt alle From-Werte inkl. Secrets ohne Rückfrage. Explizite
  Umgebungsvariablen übersteuern (`env` > `from` > lokal).

### Was wird übernommen — exakt edit + secrets

- **`.env`** (kuratierte Allowlist, nicht die ganze Datei): `AUTH_MODE`,
  `GITLAB_MODE`, `GITLAB_URL`, `BASE_IMAGE`, `BASE_DOCKERFILE`, `BASE_CONTEXT`.
  **Nicht** host-spezifische wie `DEV_UID`. **Kein `WARDEN_*`**: die Wizards
  un-setzen diese ohnehin aus `.env` (Policy lebt in `warden.toml`, als Datei
  vererbt); ein geerbtes `WARDEN_ALLOWED_PROJECTS` würde die vererbte
  `warden.toml` *still überschatten* (`docker-compose.yml:32` macht es zum
  Override) — genau der Shadow, vor dem `cmd_allow` warnt.
- **`config/`**: `image/Dockerfile`, `warden.toml`, `squid.conf`, `allowlist.txt`.
- **`secrets/`**: gitlab-tokens, anthropic-key, `claude/`.
- **Nie**: `compose.resolved.yml`, `logs/`, `state/`, `README.md`.

### Steps

1. **`cli.py`**: `--from PATH` am `init`-Subparser (koexistiert mit
   `--yes`/`--force`/`--skip-sync`).
2. **`commands/setup/__init__.py` (`cmd_init`)**: bei gesetztem `--from` Quelle
   validieren (`<path>/.catraz` vorhanden, sonst `CliError`), kuratierte
   `.env`-Keys + `config/`-Dateien + `secrets/` als `inherited` laden/stagen und
   in den Wizard durchreichen.
3. **`_wizard_interactive.py`**: Default-Quelle `inherited` > `local` > hardcoded;
   Skip-Guards behandeln „from aktiv" wie `force`; Secret-Prompts um
   „keep inherited (hidden)" erweitern.
4. **`_wizard_yes.py`**: `inherited` übernehmen; env-vars übersteuern.
5. **Tests**: neue `test_init_from` (interaktiv mit Defaults, `-y`-Klon,
   Secret-nie-angezeigt, `DEV_UID` nicht geerbt, Fehler bei ungültigem Pfad).

---

## Success criteria

1. `catraz init` legt `.catraz/config/image/Dockerfile` (`FROM ubuntu:24.04`
   +git) und `.catraz/README.md` an; der Default-Stack baut durch.
2. Basis ändern = ausschließlich `config/image/Dockerfile` editieren; `BASE_*` in
   `.env` übersteuert es (Präzedenz `BASE_IMAGE` > `BASE_DOCKERFILE` > lokal).
3. `git` ist im Container vorhanden, auch wenn die Nutzerbasis es nicht mitbringt.
4. Ein nicht-Debian-`FROM` führt zu einer Fehlermeldung, die die Debian-Annahme
   als wahrscheinliche Ursache nennt.
5. `.catraz/.auth.compose.yml` wird nicht mehr erzeugt; `compose.resolved.yml`
   enthält die Auth-Binds weiterhin; der Layered-Fallback bringt den Stack hoch.
6. `.catraz` hat die Tier-Topologie; `claude/` und `run/warden` existieren nicht
   mehr top-level. `catraz sync`, `run --remote`, Audit-Viewer funktionieren.
7. `catraz init --from ../other` zeigt interaktiv die Werte aus `../other` als
   Defaults; Enter übernimmt sie. `--from … -y` klont inkl. Secrets ohne
   Rückfrage. Geerbte Secrets werden nie im Klartext angezeigt; `DEV_UID` wird
   lokal auf `getuid()` gesetzt.
8. `pytest tests/cli/` grün (inkl. angepasster `test_init_wizard`,
   `test_compose_resolved`, `test_secrets`, `test_paths`, `test_doctor_*`,
   `test_image`, `test_run_base_image`, neue `test_init_from`).

## Risks & open questions

- **B↔C-Kopplung (gelöst)**: das Subscription-Auth-Asset referenziert den
  Claude-Credential-Pfad. Entscheidung: **B nutzt den aktuellen `claude/`-Pfad,
  C re-pointet ihn atomar** zusammen mit `claude_home`/Dirs/doctor-Hint. Das ist
  ein bewusster Zwei-Schritt (eine Asset-Zeile zweimal), hält aber `main` nach
  jedem Merge grün — die Alternative (C-Pfad schon in B) bricht Subscription-Auth
  im Fenster zwischen den Merges.
- **Lokale Tags + Builder**: `FROM mylocaltag` löst lokal auf (kein Registry-
  Zwang); aber `docker build --pull` bzw. ein buildx-`docker-container`-Treiber
  würden den lokalen Image-Store umgehen. catraz baut ohne `--pull` mit dem
  Default-Treiber — beibehalten.
- **Secret-`keep/replace`-UX (D)**: neuer sicherheitskritischer Pfad — Wert nie
  echoen; getpass-artig.
- **Migration**: bestehende `.catraz/claude` + `run/warden`. Da unreleased: harter
  Schnitt; optional einmaliger Move im `doctor --fix`.
- **`DEV_UID` nicht erben (D)**: explizite Key-Allowlist statt „ganze `.env`".

## Revision history

- v0–v4: Ideen-/Bewertungsphase (A lokales Dockerfile; B Inline-Auth; C Tiering;
  C2 verworfen; D `--from`; `ubuntu:24.04`-Default; `BASE_*` als Notausgang;
  Softlink + Auto-Cache verworfen).
- v5: Ideendokument zu **Plandokument** umgeschrieben; Idee C (leicht) gewählt,
  `.catraz/README.md`-Asset als Tierguide aufgenommen; Reihenfolge B→C→A→D.
- v6: Roast-Iteration 1 eingearbeitet. **Angenommen**: C1 (`curl`/`ca-certificates`/
  `gnupg` in claude-layer — sonst bricht Default-Build), C2 (`secrets/` 0700
  explizit, raus aus 0755-Loop), C3 (B↔C atomar, B behält `claude/`-Pfad), C4
  (`"run"` aus chown-Loop), I1 (Existenz-Guard in `resolve_base`-Default), I2
  (`test_image_assets`/`test_auth` ergänzt), I3 (`WARDEN_*` aus `--from`-
  Allowlist), I4 (doctor-Hint-Literal), NITs (B5 `.gitignore`, `auth_mode` in
  `_source_cmd`). **Abgelehnt**: keine — alle Findings am Code verifiziert.
