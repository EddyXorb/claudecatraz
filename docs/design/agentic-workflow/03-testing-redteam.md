# 03 — Teststrategie, Red-Team-Suite & CI

Konkretisierung von [`README.md`](./README.md) §8. Leitsatz: **Die Regeln sind nur so viel
wert wie ihr Nachweis.** Ziel: jede Regel R1–R6 hat benannte, automatisierte Tests, die in
CI laufen — inklusive der bewussten Umgehungsversuche aus §3. Dieser Plan bündelt die
schon in den Komponenten-Plänen verankerten Unit-/Integrationstests (W14, F10) und ergänzt
die **komponentenübergreifende End-to-End-/Red-Team-Suite** und die **CI-Pipeline**.

Status: **Implementierungs-Entwurf.** Querverweise „§x" → README, „W§x" →
[`02-warden.md`](./02-warden.md), „F§x" → [`02-forward-proxy.md`](./02-forward-proxy.md).

**Parallelität:** Präfix `03` gemeinsam mit
[`03-observability.md`](./03-observability.md). Die **Unit-/Integrationstests** entstehen
schon mit den `02`-Komponenten (W14, F10) — *test-first*. Dieser Plan deckt die **E2E-/
Red-Team-Schicht**, die den fertigen `02`-Stack voraussetzt, und ist unabhängig vom
Observability-Ausbau → gleichzeitig machbar.

---

## T.1 Testpyramide (Spiegel von §8.1)

| Ebene | Was | Werkzeug | wo geplant |
| ----- | --- | -------- | ---------- |
| **Unit** | reine `decide(request, state)`-Policy, tabellengetrieben, jede Regel | `pytest` parametrize | W5/W14 |
| **Integration API** | Warden gegen Mock-GitLab: GET-Passthrough, Merge→403, Ownership→deny, **Token nie geleakt** | `respx`/`httpx.MockTransport` | W14 |
| **Integration git** | pkt-line-Parser gegen aufgezeichnete `receive-pack`-Bodies + echtes `git push` über den Warden, **SHA-erhaltend** | `pytest` + echtes `git` | W7/W14 |
| **Quoten/Rate** | N Writes ok, N+1 blockt; Sliding-Window mit **injizierbarer Uhr** | `freezegun`/DI | W8/W14 |
| **Egress** | Allowlist erlaubt/sperrt, SNI-Spoofing abgefangen | `curl` über Squid | F10 |
| **E2E / Red-Team** | `docker compose` mit **hostile-agent**-Container gegen den ganzen Stack | testcontainers/compose | **dieser Plan, T.2** |

Die ersten fünf Ebenen sind in den Komponenten-Plänen verortet und laufen schnell
(ms–s). Dieser Plan ergänzt die langsame, aber entscheidende **E2E-Schicht**.

---

## T.2 Red-Team-Suite (`tests/redteam/`, §8.2)

Ein `docker compose`-Setup mit **Warden + Fake-GitLab + Forward-Proxy + „hostile agent"**
-Container. Der hostile Agent rennt gegen den Default-deny an; **jeder Test ist eine
erwartete Abwehr**:

| # | Angriff | Erwartung | Regel |
| - | ------- | --------- | ----- |
| A1 | `printenv` / FS-Scan nach Token | **kein** GitLab-Token auffindbar | R6 |
| A2 | direkter Connect zu `gitlab.com` | **keine Route** (Netz-Isolation) | R6 |
| A3 | Push auf `feature/x` (falsches Präfix) | reject | R2 |
| A4 | **Branch-Delete** (`new-oid` = Null) auf `claude/x` | reject (Delete nie erlaubt, W7.2) | R2 |
| A5 | Force-/non-ff-Push | reject (Warden-Präfix ok, Force via Push Rules) | R2 |
| A6 | `PUT .../merge` über **jeden** bekannten Endpoint/Alias (`merge_when_pipeline_succeeds`, `state_event=merge`) | **403** | R4 |
| A7 | Kommentar/CI-Trigger auf **fremdem** MR | deny | R3 |
| A8 | MR mit `source_branch` ohne Präfix erstellen | deny | R2/R3 |
| A9 | Flooding: Branches/MRs/Writes über die Limits | throttled/abgelehnt | R5 |
| A10 | Warden-Crash provozieren, dann Rate-Budget testen | State fail-safe „gesperrt", **kein** Reset auf 0 | R5/§6.11 |
| A11 | Exfil-POST zu nicht-allowlistetem Host über den Forward-Proxy | block + im `access.log` | §6.6 |
| A12 | Log-Assertion: jede Entscheidung erzeugt redacteten Audit-Eintrag, **kein** Token im Log | pass | §6.8 |

A4 und A10 sind die schärfsten Regressions-Wächter: A4 sichert die „Delete nie erlaubt"-
Entscheidung, A10 die fail-safe-State-Auslegung (§6.11), gegen die ein Agent gezielt
anrennen würde.

---

## T.3 Weitere Bausteine (§8.3)

- **Property/Fuzz** für den Pfad-Filter (`hypothesis`): zufällige Pfade dürfen den
  Default-deny **nie** durchbrechen.
- **pkt-line-Fuzz:** zufällige/abgeschnittene `receive-pack`-Köpfe dürfen den Parser nicht
  zum Crash oder zur Fehlfreigabe bringen (Längenpräfix-Lügen, fehlende flush-pkt).
- **Coverage-Gate** ≥ 90 % auf `policy.py` (sicherheitskritisch).
- **Regel-Traceability:** Testnamen tragen die Regel-ID (`TestR4_MergeBlocked`) → Abdeckung
  je Regel sichtbar.
- **Contract-Tests** gegen die echte GitLab-API/OpenAPI-Spec für die ~5 geprüften
  Schreib-Endpoints (§6.9): ändert sich ein Endpoint, wird ein Test rot → Drift fällt früh
  auf, statt im Betrieb ein Loch zu öffnen.

---

## T.4 CI-Pipeline (`.gitlab-ci.yml`, §8.4)

```
stages: [unit, integration, e2e]

unit:         # bei jedem Push — schnell
  - pytest tests/test_policy.py tests/test_pktline.py
  - coverage-gate policy.py >= 90%

integration:  # bei jedem Push
  - pytest tests/test_api_proxy.py tests/test_git_e2e.py tests/test_quota.py
  - contract-tests gegen GitLab-OpenAPI

e2e:          # bei MR + nächtlich (langsam, braucht Docker)
  - docker compose -f tests/redteam/compose.yml up --abort-on-container-exit
  - pytest tests/redteam/
```

- **Unit + Integration + Quoten** bei jedem Push; **E2E/Red-Team** bei MR und nächtlich.
- **Pipeline rot → Merge-Block.** So wird die Sandbox selbst unter dem Schutz von Layer 2
  (§7) entwickelt — die Regeln, die sie durchsetzt, gelten auch für ihren eigenen Code.
- Die Red-Team-Suite läuft **bei jeder Warden-/Proxy-Änderung** (§8.2), nicht nur nächtlich,
  wenn die Änderung Policy-Pfade berührt.

---

## T.5 Definition of Done

- [ ] Jede Regel R1–R6 hat mindestens einen benannten, grünen Test.
- [ ] Red-Team-Suite (T.2, A1–A12) vollständig grün und in CI verankert.
- [ ] Coverage-Gate auf `policy.py` aktiv (≥ 90 %).
- [ ] Contract-Tests gegen die geprüften Schreib-Endpoints laufen.
- [ ] CI blockt Merge bei roter Pipeline.
- [ ] Log-Assertion (A12) bestätigt: kein Token in Logs, jede Entscheidung auditiert.
