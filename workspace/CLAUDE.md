# Workspace — Kontext & Regeln für den Agenten

Du läufst als Benutzer `dev` im Container `claude-dev-env`. Der Ordner `/workspace` ist
ein **bind-mount** — Host (VSCode) und Agent teilen denselben Working-Clone. Jede
Änderung ist sofort auf dem Host sichtbar und umgekehrt.

---

## Netz & Egress

`agent-net` ist `internal: true` — du hast **keine direkte Internetroute**. Jeder
ausgehende Request muss über einen der zwei Egress-Punkte:

| Ziel                       | Weg                   | Konfiguration                                |
| -------------------------- | --------------------- | -------------------------------------------- |
| Internet (Research, Build) | Forward-Proxy (Squid) | `http_proxy` / `https_proxy` bereits gesetzt |
| GitLab                     | Warden (wenn aktiv)   | `git insteadOf` + `GITLAB_API_URL`           |

**Erlaubte Domains** (Kurzliste — vollständige Liste: `config/allowlist.txt`):
`.anthropic.com`, `.npmjs.org`, `.pypi.org`, `.crates.io`, `files.pythonhosted.org`,
`.conan.io`, `apt.llvm.org`, `sh.rustup.rs`, `static.rust-lang.org`,
`deb.nodesource.com`, `docs.gitlab.com`, `doc.rust-lang.org`, `docs.python.org`,
`stackoverflow.com`, `github.com`, `raw.githubusercontent.com`, `gitlab.com` (interim).

Domains außerhalb der Allowlist werden vom Proxy **stillschweigend geblockt** (kein DNS,
kein TCP). Überprüfe `logs/squid/access.log` auf dem Host, wenn ein Request scheitert.

---

## GitLab — was geht, was nicht

### Kein Token im Container (by design)

Du hältst **kein** GitLab-Token. Das ist absichtlich (Sicherheitsarchitektur §R6). Alle
GitLab-Operationen laufen ausschließlich über den **Warden** (`gitlab-warden:8080`), der
alle Tokens hält und die Policy erzwingt.

### Warden aktiv (COMPOSE_PROFILES=warden)

`git` ist automatisch umgeleitet — kein Unterschied in der Benutzung:

```bash
git clone https://gitlab.com/group/project.git   # geht transparent durch den Warden
git fetch && git push origin claude/mein-branch   # ebenso
```

REST-Calls (MR erstellen, CI triggern etc.) direkt gegen den Warden (`gitlab-warden:8080`):

```bash
# MR erstellen
curl -sS "http://gitlab-warden:8080/api/v4/projects/<id>/merge_requests" \
  -H "Content-Type: application/json" \
  -d '{"source_branch":"claude/mein-branch","target_branch":"main","title":"..."}'

# CI-Pipeline triggern
curl -sS -X POST "http://gitlab-warden:8080/api/v4/projects/<id>/pipeline" \
  -H "Content-Type: application/json" \
  -d '{"ref":"claude/mein-branch"}'
```

Der Warden erwartet **keine Auth** vom Agenten — Token-Injektion passiert intern.

### Warden nicht aktiv (Stufe 01 / kein Warden-Profil)

Kein Token, kein Warden → **kein Schreib-Zugriff auf GitLab**. Öffentliche Repos über
den Forward-Proxy lesbar (`git clone` / `git fetch`). Push schlägt fehl.

### Harte Grenzen (Warden erzwingt, lassen sich nicht umgehen)

| Erlaubt                                  | Verboten                                                       |
| ---------------------------------------- | -------------------------------------------------------------- |
| Push auf `claude/*`-Branches             | Push auf `main`, `develop` oder Branches ohne `claude/`-Präfix |
| MRs erstellen, kommentieren, CI triggern | MRs mergen (→ 403, immer)                                      |
| Lesen (API-GETs, git fetch/clone)        | Token aus der Umgebung lesen (keiner vorhanden)                |
| Bis zu 5 offene MRs gleichzeitig         | Mehr als 60 schreibende Aktionen/Stunde                        |

---

## Toolchain

Alle Tools sind global im `PATH`:

| Tool        | Version (aus `.env`)  | Befehl                                  |
| ----------- | --------------------- | --------------------------------------- |
| Clang/LLVM  | `CLANG_VERSION`       | `clang++`, `clang-tidy`, `clang-format` |
| Rust        | `RUST_VERSION`        | `cargo`, `rustc`, `rustfmt`, `clippy`   |
| Python / uv | `UV_VERSION`          | `python3`, `uv`, `uv run`, `uv sync`    |
| Conan       | `CONAN_VERSION`       | `conan`                                 |
| Node        | `NODE_VERSION`        | `node`, `npm`                           |
| Claude Code | `CLAUDE_CODE_VERSION` | `claude`                                |

Build-Traffic (cargo, pip, npm, conan) läuft **automatisch** über den Forward-Proxy —
kein manuelles `--proxy`-Flag nötig.

### Branch-Präfix

Alle eigenen Branches mit dem in WARDEN_BRANCH_PREFIX gesetzten Wert beginnen:

```bash
git checkout -b claude/mein-feature
```

Pushes auf andere Branch-Namen werden vom Warden abgewiesen.
