# 01 — GitLab-native Sicherungsschicht

Setup-Runbook für Layer 2 (Backstop, wenn der Warden versagt). Zero-Code, reine GitLab-Konfiguration. Parallelität: gleichzeitig mit [`01-bootstrap-hardening.md`](./01-bootstrap-hardening.md) machbar.

---

## N.1 Group Service Account

- **Group Service Account** (GitLab Premium) als Bot-Identität — kein menschlicher Account, kein `api`-Token eines Maintainers.
- Mitgliedschaft in Zielprojekt/-gruppe: **Rolle = Developer** (kann nicht auf protected Branches pushen und nicht mergen).
- Numerische **User-ID** des Service-Accounts notieren → Warden braucht sie für die R3-Ownership-Prüfung (`author.id == SERVICE_ACCOUNT_ID`).

---

## N.2 Protected Branches (Backstop R2 + R4)

- `main`/Default + Release-Branches schützen, idealerweise per **Wildcard `*`** (Catch-all):
  - *Allowed to push and merge:* **No one** (bzw. nur Maintainer/Owner).
- Bot (Developer) kann damit auf keinen geschützten Branch pushen und keinen MR mergen — auch wenn der Warden ausfällt.

---

## N.3 Push Rules (Backstop R2)

- **Branch-name**-Regex: `^claude/` — GitLab weist jeden Push auf nicht passende Branches serverseitig ab.
- Force-Push ist im Warden G1 nicht entscheidbar → wird hier abgefangen (Protected Branches + Push Rules).

---

## N.4 Merge-Sperre (Backstop R4)

- „Allowed to merge: **No one**" (außer Maintainer) auf protected Branches.
- Optional: Approval-Regeln (Approval durch jemand anderen als den Autor → Bot kann nicht selbst genehmigen und mergen).

---

## N.5 Tokens — zwei, scope-minimal

| Token | Scopes | Warden-Env |
| ----- | ------ | ---------- |
| Read-Token | `read_api`, `read_repository` | `GITLAB_READ_TOKEN` |
| Write-Token | `api` (Service-Account/Developer) | `GITLAB_WRITE_TOKEN` |

Kurze TTL (7–30 Tage), Rotation, separate Tokens je Umgebung. Nur der Warden bekommt diese — nie der Agent.

---

## N.6 Read-Scope eng setzen

Projektmitgliedschaft des Read-Tokens = **nur die Projekte, an denen der Agent arbeitet**. Dasselbe spiegelt sich in `allowed_projects` in `warden.toml`.

---

## N.7 CI-Secrets

Protected CI/CD-Variablen werden nur auf protected Branches/Tags injiziert. Da der Bot ausschließlich auf `claude/*` (nicht protected) pusht, sehen seine Pipelines keine geschützten Secrets.

---

## N.8 Beobachtbarkeit

Audit Events (Premium) auf Gruppen-/Projektebene aktivieren. Bot-Aktionen sind über den Service-Account klar zuordenbar.

---

## Checkliste

- [ ] Service Account (Developer) + User-ID notiert.
- [ ] Protected Branches inkl. Wildcard `*`, push/merge = No one.
- [ ] Push Rule `^claude/` aktiv (Push auf `feature/x` wird abgewiesen).
- [ ] Merge-Sperre aktiv.
- [ ] Read- & Write-Token mit Minimal-Scopes erzeugt, TTL gesetzt.
- [ ] Read-Token-Projektmitgliedschaft = nur Arbeits-Projekte.
- [ ] Protected CI-Variablen + Audit Events aktiv.
