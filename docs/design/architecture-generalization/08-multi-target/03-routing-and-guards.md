# 03 — Routing & Guards

**Leitet ab aus** [`../08-multi-target.md`](../08-multi-target.md) §1.1 (intern) + §2.
Lies §2 zuerst. **Hängt ab von** Schritt 01, 02.

## Ziel

`UpstreamRouter` und `host_gate` auf das Endpoint-Modell umstellen: pro `[[git.endpoint]]`
ein Upstream, Basis-URL **aus `host` + `type` abgeleitet**, `resolve(host_header)` liefert
den passenden Upstream oder `None`. Der Single-Target-Sonderfall (`implicit_host`,
`resolve_target_host`) und `Config.api_url` entfallen — jeder Host ist explizit.

## Umsetzung

Ist-Zustand: `core/transport.py::UpstreamRouter` baut Upstreams aus `host_order`/
`api_url`; `Config.resolve_target_host` hat einen `host_order`-leer→`implicit_host`-Zweig;
`core/guard.py::host_gate(host, cfg)` prüft gegen `allowed_hosts` (leer ⇒ allow-all).

1. **Basis-URL-Ableitung (`core/transport.py`).** Eine Funktion
   `base_urls(endpoint) -> (git_base, api_base|None)` je `type`:
   - `gitlab` → `https://<host>` (git), `https://<host>/api/v4` (REST).
   - `github` → `https://<host>` (git), GitHub-REST-Basis (`https://api.github.com`
     bzw. Enterprise-Form) — nur anlegen, wenn der Guard existiert; sonst siehe
     Schritt 01 (type reserviert).
   - `plain` → `https://<host>` (git), **kein** API-Base.
2. **`UpstreamRouter` (`core/transport.py`).** Aus `Config.git_endpoints` **nur** für
   Endpoints mit `access_mode != "closed"` (Schritt 02) einen `Upstream` bauen (Basis-URL
   + read/write-Token aus `HostCredentials`). `resolve(host_header)` normalisiert (Reuse
   `Config.normalize_host`) und schlägt in dieser Map nach; unbekannter/`closed`-Host →
   `None`.
3. **`host_gate` (`core/guard.py`).** Gegen `Config.allowed_hosts` (jetzt aus den
   Endpoints, Schritt 01) prüfen. Die „leer ⇒ allow-all"-Regel **entfernen**: leere
   Endpoint-Liste = echtes default-deny (R6). Ein `closed`-Endpoint ist nicht in der
   nutzbaren Router-Map → `resolve` liefert `None` → deny; stelle sicher, dass `host_gate`
   diesen Fall ebenfalls als R6 abweist (Host bekannt, aber closed → deny).
4. **`Config`-Aufräumung (`core/config.py`).** `implicit_host`, `effective_hosts`
   (Single-Target-Zweig) und `resolve_target_host`'s `host_order`-leer-Sonderfall
   entfernen bzw. auf „immer aus Endpoints" reduzieren; `api_url` entfernen (Basis-URL
   kommt aus `base_urls`). Aufrufer nachziehen.

## Nicht tun

- Keine zweite Guard-Instanz pro Host — **ein** Guard je Typ mit der Host→Upstream-Map
  (§2). Nur `__init__`/Auflösung ändern, keine neue Guard-Klasse.
- Den Katalog (Recognizer/Capabilities/Scopes) **nicht** pro Host vervielfachen — er ist
  host-unabhängig.
- Keine „leere Liste ⇒ alles erlauben"-Rückfallregel wiederherstellen.

## Tests

`warden/tests/test_host_routing.py`: `resolve` wählt pro `Host`-Header den richtigen
Upstream; unbekannter Host → `None` → R6-Deny; `closed`-Endpoint (kein Token) → `None` →
R6-Deny; `base_urls` je `type` (gitlab/plain) korrekt; leere Endpoint-Liste → alles
default-deny (kein allow-all mehr). Bestehende Routing-/Guard-Tests grün halten.

## Verifikation

`cd warden && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`

## Commit

Als `EddyXorb`, kein Co-Author-Trailer. Nachricht:

```
refactor(transport): route per endpoint, derive base-url by type
```

## Fertig-Kriterium

`UpstreamRouter` baut Upstreams aus den Endpoints mit `type`-abgeleiteter Basis-URL;
`resolve` liefert `None` für unbekannte/closed Hosts; `host_gate` ist echtes default-deny;
`implicit_host`/`resolve_target_host`-Sonderfall/`api_url` sind weg; Tests grün.

## Status

✅ Erledigt (Commits `6734606`, `32bc43b`). Literal umgesetzt, kein additiver
Zwischenschritt wie bei 01/02: `implicit_host`, `api_url`, `resolve_target_host`s
Single-Target-Zweig, `UpstreamRouter`s Single-Upstream-Branch und `host_gate`s
Allow-all-bei-leerer-Liste sind vollständig entfernt, nicht nur überlagert. Zusätzlich
zur wörtlichen 4-Punkte-Liste wurden auch die alte `[git.urls]`/`host_order`-Maschinerie
(`_parse_git_url_hosts`, `_host_slug`, `_resolve_host_credentials`,
`_additional_host_credential_problems`, `Config.credentials_for`/`host_credentials`)
ersatzlos entfernt — konsistent mit `00-index.md`s Grundregel „keine
Rückwärtskompatibilität zur alten Form" und mit 01/02s eigenen Status-Notizen, die diese
Aufräumung explizit „Schritt 03, sobald die Aufrufer umgehängt sind" zuschreiben.
`GITLAB_MODE`/`gitlab_mode` blieben unangetastet (Scope von Schritt 05).

**Nachträglich gefundener und gefixter Bug (Commit `32bc43b`):** Die erste Fassung ließ
`Config.effective_hosts` weiterhin jeden Endpoint-Host auflisten (auch `closed`), während
`UpstreamRouter` für `closed`-Endpoints keinen Upstream mehr baut. Der geteilte
Reconcile-Loop (`for_each_host_project`) rief `router.for_host(host)` ungeschützt auf,
was bei einem `closed`-Host einen unabgefangenen `KeyError` warf, den ganzen Loop abbrach
und damit `mark_reconciled` nie erreichte — ein einzelner tokenloser Endpoint sperrte so
dauerhaft den kompletten Guard für alle Hosts, im Widerspruch zu §4.2s
Isolationsversprechen. Fix: `for_each_host_project` überspringt einen Host ohne
Router-Eintrag jetzt geloggt, ohne `ok` auf `False` zu setzen. Regressionstests in
`test_host_routing.py`.
