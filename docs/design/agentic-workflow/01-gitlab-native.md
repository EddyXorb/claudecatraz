# 01 — GitLab-native Sicherungsschicht (Setup-Runbook)

Konkretisierung der **zweiten Sicherungsschicht** aus [`README.md`](./README.md) §7. Diese
Schicht ist **Zero-Code** (reine GitLab-Konfiguration), **robust** (von GitLabs eigener
Zugriffskontrolle durchgesetzt) und deckt **R1/R2/R4 nativ** ab sowie **R3 teilweise**.
Sie wird laut Roadmap (§10) **zuerst** eingerichtet — *vor* dem Warden —, damit der
Eigenbau sich nur gegen den gemessenen Rest (R5 + R3-Ownership) beweisen muss.

Status: **Implementierungs-Entwurf / Runbook.** Querverweise „§x" → README.

**Parallelität:** Präfix `01` gemeinsam mit
[`01-bootstrap-hardening.md`](./01-bootstrap-hardening.md) — beide unabhängig, gleichzeitig
machbar. Voraussetzung für `02`: Der Warden braucht den hier erzeugten Service-Account und
die beiden Tokens; der Forward-Proxy ist davon unabhängig.

---

## N.1 Identität: Group Service Account (Premium)

- **Group Service Account** als Bot-Identität anlegen (GitLab Premium) — **kein**
  menschlicher Account, **kein** `api`-Token eines Maintainers (§7.1).
- Mitgliedschaft in Zielprojekt/-gruppe mit **Rolle = Developer** (oder Custom Role mit
  minimalen Rechten). Developer kann nicht auf protected Branches pushen und — bei
  korrekter Einstellung — nicht mergen.
- Die numerische **User-ID** des Service-Accounts notieren → der Warden braucht sie für
  die R3-Ownership-Prüfung (`author.id == SERVICE_ACCOUNT_ID`, W6.2).

---

## N.2 Protected Branches → Backstop für R2 & R4

Pro Zielprojekt (oder Gruppen-weit):

- `main`/Default **und** Release-Branches schützen, idealerweise per **Wildcard `*`** als
  Catch-all, sodass jeder nicht-`claude/*`-Branch geschützt ist:
  - *Allowed to push and merge:* **No one** (bzw. nur Maintainer/Owner).
- Wirkung: Der Bot (Developer) kann auf **keinen** geschützten Branch pushen und **keinen**
  MR mergen — **auch wenn der Warden ausfällt** (§7.2).

---

## N.3 Push Rules (Premium) → Backstop für R2

- Unter **Push Rules** die **Branch-name**-Regex auf das Präfix setzen: `^claude/`.
  GitLab weist serverseitig jeden Push auf einen nicht passenden Branch ab — unabhängig
  vom Warden (§7.3). Direkter nativer Backstop für R2.
- Optional „Reject unsigned commits" / „Committer restriction" nach Bedarf.

**Zusammenspiel mit Warden-G1 (W7.5):** Force-Push ist im G1-Proxy **nicht** entscheidbar
und wird genau **hier** abgefangen — Protected Branches + Push Rules lehnen
non-fast-forward auf geschützten Refs ab. Diese native Schicht ist damit der designierte
Force-Push-Backstop.

---

## N.4 Merge-Sperre → Backstop für R4

- Projekt-Einstellung *Merge requests → wer darf mergen*: „Allowed to merge: **No one**"
  (außer Maintainer) auf protected Branches (§7.4).
- Optional **Approval Rules**, die Approval durch jemand anderen als den Autor verlangen →
  der Bot kann nicht selbst genehmigen und mergen.
- Ergebnis: Selbst mit `api`-Token kann der Bot keinen Merge auslösen — doppelte
  Sicherung neben dem Warden-Merge-Block (W6.3).

---

## N.5 Tokens → zwei, scope-minimal

Genau **zwei** Tokens erzeugen, die **nur** der Warden hält (nie der Agent, §7.5):

| Token | Scopes | Zweck | Warden-Env (W10) |
| ----- | ------ | ----- | ---------------- |
| Read-Token  | `read_api`, `read_repository` | Lesepfad (REST-GET + git-fetch) | `GITLAB_READ_TOKEN` |
| Write-Token | `api` | API-Writes (MR/Note/CI) + git-push | `GITLAB_WRITE_TOKEN` |

- „Nur Claude-Branches" kommt **nicht** aus dem Token-Scope (GitLab hat keinen feineren
  „nur MR"-Scope), sondern aus Warden **+** Push Rules/Protected Branches.
- **Kurze TTL** (7–30 Tage), Rotation, separate Tokens je Umgebung.

---

## N.6 Read-Scope eng setzen (§6.10, Roadmap §10.5)

Die Lese-**Reichweite** ist eine Exfil-Fläche (§6.10) — bewusst klein halten:

- Projektmitgliedschaft des **Read-Tokens** = **nur die Projekte, an denen der Agent
  arbeitet**, nicht die ganze Gruppe. Wirksamste Einzelmaßnahme gegen Exfil-Reichweite.
- Dieselbe Liste spiegelt sich in der Warden-`ALLOWED_PROJECTS` (W10) → zwei Schichten
  derselben Begrenzung.
- Projektübergreifendes Leserecht ist eine **bewusste, dokumentierte** Entscheidung, kein
  Default.

---

## N.7 CI-Secrets → natürliche Sperre

- **Protected CI/CD-Variablen** verwenden — sie werden nur auf protected Branches/Tags
  injiziert. Da der Bot ausschließlich auf `claude/*` (nicht protected) pushen kann, sehen
  seine Pipelines **keine** geschützten Secrets (§7.6). Begrenzt den Schaden eines
  getriggerten Jobs.

---

## N.8 Beobachtbarkeit

- **Audit Events** (Premium) auf Gruppen-/Projektebene aktivieren; Bot-Aktionen sind über
  den Service-Account klar zuordenbar und alarmierbar (z. B. unerwarteter Merge-Versuch,
  §7.7). Ergänzt das Warden-Audit-Log (§6.8 / [`03-observability.md`](./03-observability.md))
  um die GitLab-seitige Sicht.

---

## N.9 Offene Verifikationspunkte

- ⚠️ **Push-Rules-/Protected-Branch-Semantik** auf gitlab.com im konkreten Setup
  verifizieren — UI-Pfade und genaue Wirkung können sich ändern (§9). Insb. dass Wildcard
  `*` wirklich alle Nicht-`claude/*`-Branches als geschützt erfasst.
- ⚠️ Verifizieren, dass **Developer-Rolle + „merge: No one"** den Self-Merge auch über
  API-Aliasse (`merge_when_pipeline_succeeds`) blockt.
- ⚠️ **Custom Role** prüfen, falls Developer mehr Rechte gibt als nötig.

---

## N.10 Regel → native Durchsetzung (Spiegel von §7.8)

| Regel | Nativ durchgesetzt durch |
| ----- | ------------------------ |
| R1 | `read_api`/`read_repository`-Scope (N.5) |
| R2 | Push Rules `^claude/` + Protected Branches `*` (N.2/N.3) |
| R3 | Developer-Rolle (kein Schreibrecht außerhalb eigener Branches) — *Ownership-Präzision bleibt Warden, W6.2* |
| R4 | Protected Branches „merge: No one" + Approval-Regeln (N.4) |
| R5 | *nativ nur grob (Plattform-Rate-Limits) — Warden ist primär (W8)* |
| R6 | Minimal-Scopes + Service-Account-Rolle begrenzen Worst-Case-Schaden |

---

## N.11 Definition of Done

- [ ] Service Account (Developer) + numerische User-ID notiert.
- [ ] Protected Branches inkl. Wildcard `*`, push/merge = No one.
- [ ] Push Rule `^claude/` aktiv und getestet (Push auf `feature/x` wird abgewiesen).
- [ ] Merge-Sperre + ggf. Approval-Regel aktiv.
- [ ] Read- & Write-Token mit Minimal-Scopes erzeugt, TTL gesetzt, an den Warden-Betreiber
      übergeben (nicht in den Agenten!).
- [ ] Read-Token-Projektmitgliedschaft = nur Arbeits-Projekte.
- [ ] Protected CI-Variablen, Audit Events aktiv.
