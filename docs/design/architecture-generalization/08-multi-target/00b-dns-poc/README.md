# DNS-Aliasing + Host-Header-Routing PoC

Siehe [`../00b-dns-poc.md`](../00b-dns-poc.md) für Hintergrund und Ziel.

## Reproduktion

```sh
cd docs/design/architecture-generalization/08-multi-target/00b-dns-poc

# Stack starten (echo-Server + client-Container auf gemeinsamem Netz)
docker compose up -d

# (1)+(2): beide Aliase erreichen denselben Server, Host-Header korrekt
docker compose exec client sh -c 'wget -qO- http://alpha.test:8080/'
docker compose exec client sh -c 'wget -qO- http://beta.test:8080/'

# (3): nicht aliasierter Host löst nicht auf
docker compose exec client sh -c 'wget -qO- http://gamma.test:8080/ ; echo "exit=$?"'

# (4): Schema-Rewrite-Prämisse mit echtem git
docker compose exec client sh -c '
  git config --global url."http://alpha.test:8080/".insteadOf "https://alpha.test/"
  GIT_CURL_VERBOSE=1 git ls-remote https://alpha.test/whatever.git 2>&1 | head -40
'

# Aufräumen
docker compose down
```

## Ergebnisse

| # | Check                                        | Ergebnis                                                                                                    |
|---|-----------------------------------------------|---------------------------------------------------------------------------------------------------------------|
| 1 | `alpha.test:8080`                             | `Host: alpha.test:8080` — grün                                                                                  |
| 2 | `beta.test:8080`                              | `Host: beta.test:8080` — grün, beide Aliase treffen denselben Container                                        |
| 3 | `gamma.test:8080` (nicht aliasiert)           | `wget: bad address 'gamma.test:8080'`, exit=1 — grün, Default-Deny hält                                        |
| 4 | `insteadOf`-Rewrite `https://alpha.test/` → `http://alpha.test:8080/` | Request geht als Klartext-HTTP raus (kein TLS-Handshake im Verbose-Log), `Host: alpha.test:8080` — grün |

Alle vier Annahmen aus §1.1/§1.2 von [`../08-multi-target.md`](../08-multi-target.md) sind damit
empirisch bestätigt. Der `fatal: ... not valid: is this a git repository?` am Ende von Check 4 ist
erwartet: der Echo-Server ist kein echtes Git-Backend, das war nicht Teil der Prämisse — geprüft
wurde nur Routing/Host-Header/Rewrite.

**Fazit:** Die Compose-/Routing-Prämisse trägt; 01–08 können ohne weiteres Risiko in dieser Hinsicht
angegangen werden.
