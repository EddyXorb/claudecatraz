# 08 — Container-Test: Multi-Host-Erreichbarkeit

**Leitet ab aus** [`../08-multi-target.md`](../08-multi-target.md) §8 (Fertig-Kriterium des
Gesamtschritts). **Hängt ab von** Schritt 01–07 (setzt echte, per Compose verdrahtete
DNS-Aliase voraus, nicht nur Unit-Mocks).

## Ziel

Ein Integrationstest belegt end-to-end, dass ein `.catraz` **zwei** gelistete Hosts über
denselben Warden bedient und einen **dritten**, nicht gelisteten Host abweist — der
verbleibende offene Punkt aus dem Hauptdokument §8.

## Umsetzung

Ist-Zustand: `tests/container/` fährt heute den Warden gegen **einen** Upstream. Erweitern
auf zwei.

1. **Zwei Endpoints konfigurieren.** `warden.toml` mit zwei `[[git.endpoint]]` (z.B. zwei
   Test-/Mock-Upstreams) plus Tokens in `read_tokens`/`write_tokens`; per Compose DNS-Aliase
   für beide Hosts auf den Warden (Schritt 07).
2. **Beide erreichbar.** Für jeden der zwei Hosts einen repräsentativen Request (git-Pfad
   **und** REST) über den `Host`-Header schicken und einen erlaubten Ausgang (bzw. korrektes
   Upstream-Targeting) verifizieren; die per-Endpoint-Trennung (State/Quote, Schritt 04)
   stichprobenhaft prüfen.
3. **Dritter abgelehnt.** Ein Request mit einem `Host`, der in keinem Endpoint steht → R6
   default-deny.
4. **Closed-Endpoint (optional, wenn günstig).** Ein Endpoint ohne Token startet `closed`
   (deny), ohne den Warden/die anderen Endpoints zu stören (§4.2 fail-closed-degrade).

## Nicht tun

- Keine echten externen Forge-Instanzen/Secrets im Test — Mock-/Test-Upstreams.
- Den Test nicht als reinen Unit-Mock bauen; er soll die **compose-verdrahteten**
  DNS-Aliase über den echten `Host`-Header ausüben (dafür ist er die Ergänzung zu den
  Unit-Tests der Schritte 01–07).

## Tests

Dies **ist** der Test (`tests/container/test_multi_host.py` o.ä.): zwei Hosts erreichbar,
dritter default-deny; optional der closed-Endpoint-Fall.

## Verifikation

`uv run --with pytest python -m pytest tests/cli/ tests/container/ -q && uv run mypy`

## Commit

Als `EddyXorb`, kein Co-Author-Trailer. Nachricht:

```
test(container): multi-host reachability + default-deny
```

## Fertig-Kriterium

Der Container-Test zeigt zwei gelistete Hosts erreichbar und einen nicht gelisteten
abgelehnt; er läuft grün. Damit ist der Gesamtschritt (Hauptdokument §8) erfüllt und Punkt 8
in `07-offene-verbesserungen.md` kann als erledigt markiert werden.
