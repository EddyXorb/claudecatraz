# Git Smart-HTTP — Protokoll-Referenz

Git kommuniziert über HTTP mit genau drei Endpunkten. Zwei davon sind ein
Handshake (Ref Advertisement), einer führt die eigentliche Operation durch
(Pack-Transfer). Das nennt sich **Smart HTTP**.

## URL, Remote und Repo-Pfad

Ein Git-Remote ist eine URL in `.git/config`:

```text
[remote "origin"]
    url = https://warden.example.com/git/group/proj.git
```

Die URL hat zwei Teile, die Git trennt:

- **Host** — `warden.example.com` (oder `gitlab.com` bei direktem Zugriff).
  Git öffnet eine TCP-Verbindung auf Port 443 (HTTPS) oder 80 (HTTP).
- **Pfad** — alles danach, hier `/git/group/proj.git`. Das ist das `{repo}`
  in allen Smart-HTTP-Endpunkten.

Git hängt dann die Endpunkt-Suffixe an diesen Pfad an:

```text
https://warden.example.com/git/group/proj.git/info/refs?service=git-receive-pack
https://warden.example.com/git/group/proj.git/git-receive-pack
```

Bei direktem GitLab-Zugriff (`https://gitlab.com/group/proj.git`) entfällt
der `/git/`-Prefix — GitLab mounted den Git-Endpunkt direkt unter dem Root.
Der Warden braucht diesen Prefix, weil er auf Port 8080 Git-Routen
(`/git/…`) und API-Routen (`/api/v4/…`) gemeinsam exposed.

Die Remote-URL in `.git/config` bleibt **kanonisch** (`https://gitlab.com/…`)
— identisch zum normalen Clone. Die Umleitung auf den Warden steht
ausschließlich in der globalen Container-`~/.gitconfig` und wird beim
Container-Start einmalig gesetzt:

```text
git config --global \
  url."http://gitlab-warden:8080/git/".insteadOf "https://gitlab.com/"
```

Damit greift der Rewrite nur im Container (wo `~/.gitconfig` existiert),
nicht auf dem Host (anderes `$HOME`). `git remote -v` zeigt im Container
die umgeschriebene Adresse; `.git/config` und Host bleiben unberührt.
Der `/git/`-Prefix im Rewrite-Ziel ist nötig, damit das Routing auf Port
8080 funktioniert.

## HTTP kurz erklärt

HTTP ist ein Klartext-Anfrage-Antwort-Protokoll über TCP (in der Praxis
fast immer als HTTPS über TLS). Jede Anfrage besteht aus:

```text
GET /git/group/proj.git/info/refs?service=git-receive-pack HTTP/1.1
Host: warden.example.com
Authorization: Basic <base64(user:token)>
Git-Protocol: version=2         ← optionaler Capability-Header
                                ← Leerzeile = Ende der Headers
```

Die Antwort:

```text
HTTP/1.1 200 OK
Content-Type: application/x-git-receive-pack-advertisement
                                ← Leerzeile
<pkt-line Body>
```

Für Git-Operationen verwendet HTTP zwei Methoden:

- `GET` — Ref Advertisement (`info/refs`), kein Body
- `POST` — Pack-Transfer (`git-upload-pack`, `git-receive-pack`), binärer Body

Der `Content-Type` identifiziert dabei eindeutig, um welchen Git-Endpunkt es
sich handelt (`application/x-git-upload-pack-request` usw.). Ein normaler
HTTP-Proxy ohne Git-Kenntnis würde diese Requests einfach durchreichen — der
Warden hingegen parst den Body aktiv.

## Der Repo-Pfad im Warden

In allen drei Endpunkten steht `{repo}` für den vollständigen Pfad des
Repositories auf dem Server — ohne führenden Slash, mit `.git`-Suffix.
Bei GitLab ist das der Namespace-Pfad: `group/projekt.git` für ein
Top-Level-Repo, `group/subgroup/projekt.git` für ein Subgroup-Repo.
Beliebig viele Namespace-Ebenen sind möglich.

Der Warden mounted die Endpunkte unter dem Prefix `/git/` und fängt den
Repo-Pfad mit dem Starlette-Converter `:path`, der im Gegensatz zu
`:str` auch Slashes matcht:

```text
GET  /git/{project:path}/info/refs
POST /git/{project:path}/git-upload-pack
POST /git/{project:path}/git-receive-pack
```

`project` in `path_params` enthält dann z.B. `group/subgroup/projekt.git`.
Die Config-Methode `project_allowed()` entfernt das `.git`-Suffix vor dem
Allowlist-Vergleich.

## Endpunkte

### GET `/{repo}/info/refs?service=<service>`

Ref Advertisement — der Handshake. Der Client fragt, welche Refs (Branches,
Tags) der Server kennt und welche Capabilities er unterstützt. `service` ist
`git-upload-pack` für Fetch/Clone oder `git-receive-pack` für Push.

Der Server antwortet mit einem pkt-line-Stream:

```text
001e# service=git-upload-pack\n    ← Service-Header
0000                                ← Flush (Ende des Headers)
00b4<sha1> HEAD\0side-band-64k ...  ← erste Ref + Capabilities (NUL-getrennt)
003f<sha1> refs/heads/main\n
0000                                ← Flush (Ende der Ref-Liste)
```

Der Warden reicht diesen Endpunkt vollständig durch — nach dem Allowlist-Check
ohne weitere Inspektion. Fetch/Clone verwenden den Read-Token, Push den
Write-Token (anhand von `service`).

### POST `/{repo}/git-upload-pack`

Fetch / Clone. Der Client schickt eine `want`-Liste (welche Commits er
haben möchte) und eine `have`-Liste (was er schon hat). Der Server antwortet
mit einem Packfile das genau den fehlenden Delta enthält.

```text
0032want <sha1> side-band-64k ofs-delta\n
0000
0009done\n
```

Response bei `side-band-64k` multiplext über drei Kanäle: `\x01` Packfile-Daten,
`\x02` Progress (stderr beim Client), `\x03` fataler Fehler.

Warden reicht vollständig durch — nach Allowlist-Check mit Read-Token, keine
Inspektion des Bodies.

### POST `/{repo}/git-receive-pack`

Push. Der Request-Body besteht aus zwei Abschnitten, getrennt durch ein
Flush-Packet:

```text
Abschnitt 1 — Ref-Commands (vom Warden geparsed):
00a9<old-sha1> <new-sha1> refs/heads/claude/x\0report-status side-band-64k\n
0000   ← Flush

Abschnitt 2 — Packfile (vom Warden nie gepuffert):
PACK....
```

Der Warden liest nur Abschnitt 1 vollständig (bis zum Flush), policed die
Ref-Commands, und streamt dann den unveränderten Body (Abschnitt 1 + 2) weiter.
Das Packfile wird zu keinem Zeitpunkt vollständig im Speicher gehalten.

## pkt-line Format

Jedes Paket beginnt mit einer 4-stelligen Hex-Zahl, die die Gesamtlänge des
Pakets inklusive der 4 Längen-Bytes selbst angibt:

```text
0006a\n   ← Länge 6, Inhalt "a\n"
0000      ← Flush-Packet (Länge 0) — Trennmarkierung zwischen Abschnitten
```

`0001` (delimiter) und `0002` (response-end) sind Protokoll-v2-Erweiterungen,
die der Warden nicht auswertet.

## Vollständiges Beispiel: git push

```text
git push origin claude/feature
```

### Schritt 0 — Remote-URL nachschlagen

Git liest `.git/config`, findet `url = https://warden.example.com/git/group/proj.git`
und baut den Host (`warden.example.com:443`) und den Repo-Pfad (`group/proj.git`)
daraus.

### Schritt 1 — Ref Advertisement (GET)

```text
→ GET /git/group/proj.git/info/refs?service=git-receive-pack HTTP/1.1
     Host: warden.example.com
     Authorization: Basic <credentials>

← 200 OK  Content-Type: application/x-git-receive-pack-advertisement
     001e# service=git-receive-pack\n
     0000
     00b4<sha-main> refs/heads/main\0report-status side-band-64k\n
     003c<sha-old>  refs/heads/claude/feature\n
     0000
```

Der Warden prüft hier: Projekt in Allowlist? Token-Injektion (Write-Token
statt Agent-Credentials). Dann Weiterleitung an GitLab.

Git vergleicht nun die beworbenen Refs mit dem lokalen Stand und stellt fest:
`claude/feature` zeigt auf `<sha-old>`, lokal ist es jetzt `<sha-new>` —
es gibt also etwas zu pushen.

### Schritt 2 — Push (POST)

Git packt die fehlenden Objekte in ein Packfile und schickt:

```text
→ POST /git/group/proj.git/git-receive-pack HTTP/1.1
     Content-Type: application/x-git-receive-pack-request

     00a9<sha-old> <sha-new> refs/heads/claude/feature\0report-status side-band-64k\n
     0000          ← Ende Abschnitt 1
     PACK<binäre Objektdaten>   ← Abschnitt 2
```

Der Warden liest Abschnitt 1 vollständig, parst die Ref-Commands und ruft
`decide()` auf:

- R6: `group/proj` in Allowlist? ✓
- R2: `claude/feature` hat Prefix `claude/`? ✓
- R2: kein Delete (old ≠ 0000…)? ✓
- R5: Quotas (Branches, Rate, Lock)? ✓

Alle Checks bestehen → Warden schreibt Audit-Log-Eintrag, injiziert den
Write-Token, und streamt den kompletten Body (Abschnitt 1 + 2) an GitLab weiter.

### Schritt 3 — Antwort an den Client

GitLab antwortet mit einem pkt-line-Stream, den der Warden ungepuffert
durchreicht:

```text
← 200 OK
     0030\x01unpack ok\n           ← Packfile wurde entpackt
     0036\x01ok refs/heads/claude/feature\n  ← Ref akzeptiert
     0000
```

Der Git-Client zeigt:

```text
To https://warden.example.com/git/group/proj.git
   sha-old..sha-new  claude/feature -> claude/feature
```

Bei einem Policy-Verstoß (z.B. falscher Prefix) antwortet der Warden
selbst mit `403` — ohne den Request an GitLab weiterzuleiten.

## Sonderfälle

**Git LFS** — Objekte werden über einen separaten Endpunkt übertragen
(`/{repo}/info/lfs/objects/batch`). Dieser ist im Warden nicht gemountet;
LFS-Requests fallen in den API-Proxy oder erhalten eine 404.

**Push Certificates** (`git push --signed`) — fügen vor dem ersten Flush eine
zusätzliche pkt-line-Sektion in den receive-pack-Body ein. `parse_commands`
liest bis zum Flush; das Zertifikat landet ungeparsed im `head`-Puffer und
wird ungeprüft weitergeleitet.

**Git-Protokoll v2** — ab git 2.26 kann der Client den Header
`Git-Protocol: version=2` schicken, der einen effizienteren Handshake
aktiviert. Der Warden leitet diesen Header nicht explizit weiter; GitLab
fällt in dem Fall auf Protokoll v1 zurück.

**SSH** — `git+ssh://` ist ein eigenes Protokoll und liegt vollständig
außerhalb des Warden-Scopes.
