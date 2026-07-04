# 00b — (Optional) Wegwerf-PoC: Docker-DNS-Aliasing + Host-Header-Routing

**Leitet ab aus** [`../08-multi-target.md`](../08-multi-target.md) §1.1/§1.2. **Optional
und vorgezogen** — kein Produktschritt. Er validiert die eine *physische* Annahme, die nur
ein echter Lauf beweist, bevor in 01–07 investiert wird. Er **gated nicht** die
Warden-internen Schritte 01–05 (die sind unabhängig vom DNS-Mechanismus korrekt); er
de-risked nur die Compose-/Routing-Schicht (07/08).

## Ziel

Empirisch bestätigen (nicht per Mock):

1. **DNS-Aliasing:** mehrere kanonische Hostnamen zeigen aus dem Client-Container auf
   **einen** Ziel-Container.
2. **Host-Header trägt das Ziel:** der Server sieht `Host: <host>:8080` und kann daran
   routen.
3. **Default-deny am Netz:** ein nicht aliasierter Host löst nirgends auf.
4. **Schema-Rewrite-Prämisse:** ein kanonischer `https://<host>/`-git-Remote landet via
   `insteadOf` (`→ http://<host>:8080/`) als Klartext beim Ziel, mit korrektem `Host`.

## Aufbau (throwaway, außerhalb des Produktbaums)

Alles in ein Scratch-Verzeichnis legen (z.B. `/tmp/…/dns-poc/`), **nicht** unter
`src/catraz/` oder `warden/`. Zwei Services auf einem user-defined Netzwerk:

- **echo** — ein Server auf `:8080`, der den empfangenen `Host`-Header zurückgibt (z.B.
  ein 5-Zeilen-`http.server`-Subclass, der `self.headers["Host"]` schreibt; alternativ das
  `kennethreitz/httpbin`-Image, `/headers` echot den Host). Auf dem Netzwerk mit
  **Netzwerk-Aliassen** `alpha.test` **und** `beta.test`:

  ```yaml
  services:
    echo:
      # image/build: minimaler Host-Echo-Server auf :8080
      networks:
        poc-net:
          aliases: ["alpha.test", "beta.test"]
    client:
      image: alpine/git   # hat git + wget/curl
      command: ["sleep", "infinity"]
      networks: ["poc-net"]
  networks:
    poc-net: {}
  ```

- **client** — führt die Checks aus (interaktiv via `docker compose exec client sh`).

## Durchführung (im client-Container)

```sh
# (1)+(2) beide Aliase erreichen denselben Server, Host-Header korrekt:
wget -qO- http://alpha.test:8080/  # erwartet: Host = alpha.test:8080
wget -qO- http://beta.test:8080/   # erwartet: Host = beta.test:8080

# (3) nicht aliasierter Host löst nicht auf:
wget -qO- http://gamma.test:8080/ ; echo "exit=$?"   # erwartet: DNS-Fehler / kein Route

# (4) Schema-Rewrite-Prämisse mit echtem git:
git config --global url."http://alpha.test:8080/".insteadOf "https://alpha.test/"
GIT_CURL_VERBOSE=1 git ls-remote https://alpha.test/whatever.git 2>&1 | head
# erwartet: der Request geht als http nach alpha.test:8080 (Host: alpha.test:8080),
# kein TLS-Handshake — der Server-Log/Echo zeigt den Host.
```

## Ergebnis-Interpretation

- **Grün** (alle vier stimmen) → die Compose-/Routing-Prämisse aus §1 trägt; 01–08 ohne
  weiteres Risiko angehen.
- **Rot** (Aliasing greift nicht, Host-Header fehlt/falsch, oder der Rewrite hält nicht) →
  **vor** Schritt 07 klären; ggf. §1.1/§1.2 des Hauptdokuments überdenken (z.B.
  `extra_hosts` statt Netzwerk-Alias, oder Port-/Rewrite-Details). Die Warden-internen
  Schritte 01–05 bleiben davon unberührt.

## Nicht tun / Commit

- **Kein** Produkt-Commit. Der PoC ist Wegwerf-Code und bleibt aus `src/catraz/` und
  `warden/` heraus (Scratch-Verzeichnis). Nach der Erkenntnis löschen.
- Wenn du das **Ergebnis** festhalten willst: ein kurzer Satz in
  [`../08-multi-target.md`](../08-multi-target.md) §8 (Umsetzungsstand) genügt — dann als
  `EddyXorb` committen, **kein** Co-Author-Trailer. Der PoC-Code selbst wird nicht
  eingecheckt.
