# 05 — Container-Test: zwei Hosts mit unterschiedlichen `actions`

**Leitet ab aus** [`../09-endpoint-actions.md`](../09-endpoint-actions.md) §8
(Fertig-Kriterium des Gesamtschritts). **Hängt ab von** Schritt 01 (Katalog), 02
(Config), beiden Schritten 03 (Guards) und 04 (Template/`doctor`).

## Ziel

Ein Integrationstest belegt end-to-end, dass ein Multi-Endpoint-Deployment zwei Hosts
mit **unterschiedlichen** `actions` tatsächlich unterschiedlich behandelt — der harte
Punkt aus dem Hauptdokument §8. Ohne diesen Test **und** den restlosen Entfall von
`[api.endpoints]` (Schritt 03) gilt 09 als nicht erledigt.

## Ist-Zustand

- `tests/container/` fährt den Warden per Compose gegen echte, DNS-verdrahtete
  Upstreams (aus 08-Schritt-07/08). Der 08-Multi-Host-Test (zwei Hosts erreichbar,
  dritter default-deny) ist die Blaupause — 09 erweitert ihn um die **Action**-Achse.

## Umsetzung

Drei Endpoints gemäß §6 (Gesamtbild) konfigurieren und je einen Request pro Achse
üben, alles über den echten `Host`-Header (kein reiner Unit-Mock):

1. **Voller Endpoint** (`gitlab`, Default-`actions`): git-push **und** MR-Create
   (REST) werden **erlaubt** (bzw. korrekt ans Upstream geroutet).
2. **Review-only-Endpoint** (`gitlab`, `actions = ["git.fetch", "mr.comment"]`):
   - `git.fetch` (advertise-upload/upload-pack) **erlaubt**.
   - `git.push` (advertise-receive) **denied** — sauber schon in der advertise-Phase
     (Schritt 03 git-Guard).
   - MR-Create (`POST .../merge_requests`) **denied** (Recognizer nicht in der
     per-Host-Tabelle, Schritt 03 REST-Guard).
   - MR-Kommentar (`mr.comment`-Pfad) **erlaubt** — belegt, dass die Verengung
     selektiv ist, nicht pauschal.
3. **Plain-Endpoint** (`type = "plain"`, erbt Default ∩ type): `git.fetch`/`git.push`
   **erlaubt**, aber ein REST-`mr.*`-Request ist auf diesem Host ohnehin nicht
   sinnvoll/erlaubt (kein Forge-Guard-Pfad) — stichprobenhaft prüfen, dass der
   Plain-Host fetch/push kann.

Damit sind alle drei Kaskaden-Fälle aus §6 abgedeckt: voller Default, verengender
Override, geerbter `type`-Schnitt.

## Nicht tun

- **Keine** echten externen Forge-Instanzen/Secrets — Mock-/Test-Upstreams wie im
  08-Container-Test.
- **Kein** reiner Unit-Mock — der Test soll die **compose-verdrahteten** DNS-Aliase
  über den echten `Host`-Header ausüben; die per-Host-Trennung der Actions ist genau
  das, was Unit-Tests (Schritt 03) allein nicht end-to-end belegen.
- **Kein** `[api.endpoints]` in der Test-`warden.toml` — es existiert nicht mehr
  (Schritt 03). Falls doch, ist Schritt 03 unvollständig — melde das.

## Tests

Dies **ist** der Test (`tests/container/test_multi_host_actions.py` o.ä.): drei
Endpoints, die drei Kaskaden-Fälle, pro Fall der erlaubte/denied Ausgang auf der
git- **und** REST-Achse.

## Verifikation

`uv run --with pytest python -m pytest tests/cli/ tests/container/ -q && uv run mypy`

## Commit

Als `EddyXorb`, kein Co-Author-Trailer. Nachricht:

```
test(container): per-host actions — full, review-only, plain endpoints
```

## Fertig-Kriterium

Der Container-Test zeigt drei Endpoints (voll / review-only / plain) mit
unterschiedlichen effektiven `actions`, die tatsächlich unterschiedlich behandelt
werden — git- und REST-Achse, erlaubt und denied; er läuft grün. Zusammen mit dem
restlosen `[api.endpoints]`-Entfall (Schritt 03) ist damit der Gesamtschritt
(Hauptdokument §8) erfüllt.
