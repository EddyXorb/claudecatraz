# 08 Multi-Target — Umsetzung (für implementierende Agents)

Diese Dokumente leiten **exakt** aus dem Hauptdokument
[`../08-multi-target.md`](../08-multi-target.md) ab. Menschen lesen nur das
Hauptdokument (das *Was/Warum*); jeder Schritt hier ist das *Wie* eines Abschnitts
daraus. Bei Widerspruch gewinnt das Hauptdokument — melde den Widerspruch, statt zu
raten.

## Reihenfolge (Abhängigkeiten)

| # | Datei | Leitet ab aus | Hängt ab von | Status |
| --- | --- | --- | --- | --- |
| 0b | [`00b-dns-poc.md`](00b-dns-poc.md) — **optional**, vorgezogen | §1.1/§1.2 | — | übersprungen (optional, Wegwerf-PoC) |
| 1 | [`01-config-schema.md`](01-config-schema.md) | §3.1–§3.4 | — | ✅ erledigt (60abceb) |
| 2 | [`02-credentials-and-access-mode.md`](02-credentials-and-access-mode.md) | §4 | 1 | ✅ erledigt (fac1806) |
| 3 | [`03-routing-and-guards.md`](03-routing-and-guards.md) | §1.1 (intern), §2 | 1, 2 | ✅ erledigt (6734606, 32bc43b) |
| 4 | [`04-state-keying.md`](04-state-keying.md) | §5 | 1, 3 | ✅ erledigt (c7cbfbf) |
| 5 | [`05-env-cleanup.md`](05-env-cleanup.md) | §3.5 | 1, 2, 3 | ✅ erledigt (069aad9) |
| 6 | [`06-cli-doctor-init.md`](06-cli-doctor-init.md) | §6 | 1, 2 | ✅ erledigt (acbfc87) |
| 7 | [`07-compose-and-agent-routing.md`](07-compose-and-agent-routing.md) | §1.1, §1.2, §4.1 | 1–6 | ✅ erledigt (ea0dfdc) |
| 8 | [`08-container-test.md`](08-container-test.md) | §8 | 1–7 | offen |

Schritte 1–5 sind das Warden-Python-Paket (`warden/`), 6–8 die CLI-/Asset-Schicht
(`src/catraz/`). Jeder Schritt ist ein eigener Commit; **nicht** mehrere Schritte in
einem Commit vermischen.

## Gemeinsamer Arbeitsablauf (für jeden Schritt gleich)

1. Das referenzierte `§` im Hauptdokument lesen **und** die im Schritt genannten
   Ist-Dateien, bevor du etwas änderst.
2. Änderung machen.
3. **Tests schreiben** (jeder Schritt nennt welche) — das Verhalten muss durch Tests
   belegt sein, nicht nur durch Augenschein.
4. Verifikation laufen lassen:
   - Warden-Schritte (1–5):
     ```bash
     cd warden
     uv run pytest -q
     uv run ruff check .
     uv run ruff format --check .
     uv run mypy
     ```
   - CLI-/Asset-Schritte (6–8):
     ```bash
     uv run --with pytest python -m pytest tests/cli/ tests/container/ -q
     uv run mypy
     ```
5. Diff-Review.
6. **Ein Commit**, committe als Repo-Identität **`EddyXorb`** (schon so konfiguriert;
   nichts umstellen). **Kein** Co-Authorship-/`Generated-with`-Trailer. Der genaue
   Commit-Text steht in jedem Schritt unter „Commit".

## Namens- und Grundregeln (gelten überall)

- Config-Taxonomie: Domäne `git`; `[git.rules]` (Defaults) + `[[git.endpoint]]`-Array
  (ein Host je Eintrag); `type ∈ {gitlab, github, plain}` als **Feld**.
- Secrets: `read_tokens` / `write_tokens` (gruppiert, `<host> <token>`).
- Fail-**closed**: strukturelle Config-Fehler brechen den Start ab; Per-Endpoint-
  Credential-Probleme schließen nur den Endpoint (deny-all), nie den ganzen Warden.
- Kein Migrationspfad (State ist pre-1.0 wegwerfbar); keine Rückwärtskompatibilität zur
  alten `[git.urls]`/`GITLAB_MODE`/`GITLAB_URL`-Form — sie wird ersatzlos entfernt.
