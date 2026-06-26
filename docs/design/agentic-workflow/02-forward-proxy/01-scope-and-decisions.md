# 02-Forward-Proxy · 01 — Auftrag, Produktwahl & Grundsatzentscheidungen

Teil von [`../02-forward-proxy.md`](../02-forward-proxy.md). Legt fest, **was** der
Forward-Proxy tut, **womit** er gebaut wird und die zentrale **TLS-Entscheidung** — die
Grundlage, auf der die übrigen Teile (Netz, Config, Betrieb) aufsetzen.

Querverweise: „§x" → [`../README.md`](../README.md), „W§x" → [`../02-warden.md`](../02-warden.md).

---

## 1. Auftrag & Abgrenzung

Der Forward-Proxy ist der **zweite, vom Warden getrennte** Egress-Punkt (§6.1/§6.6): Über
ihn darf der Agent zum Recherchieren und Bauen ins Internet — aber nur zu einer kuratierten
Domain-Allowlist. Er hält **keine** Credentials und ist **keine** R1–R6-Komponente; er
adressiert allein **Exfiltration & Supply-Chain** (§3, „Internet ≠ GitLab-Macht").

**Was er tut:** ausgehende HTTP/HTTPS-Verbindungen des Agenten gegen eine
**Domain-Allowlist** filtern (default-deny), damit Paket-Registries, Toolchains und Doku
erreichbar sind — und sonst nichts.

**Was er ausdrücklich *nicht* tut:**

- **Keine GitLab-Vermittlung** — das ist der Warden. GitLab läuft an ihm vorbei
  (`no_proxy=gitlab-warden`).
- **Keine Credentials** — er filtert nur Ziele, hält keine Tokens.
- **Kein TLS-MITM** als Default — er entschlüsselt den Verkehr **nicht** (Begründung §3).
- **Keine Sicherheitsgrenze für R1–R6** — selbst voll kompromittiert kann der Agent über
  ihn nichts gegen `gitlab.com` tun (kein Token).

**Ehrliches Restrisiko (aus §6.6):** Allowlistete Hosts mit Schreib-/Echo-Eigenschaften
(GitHub-Gists, Such-Endpunkte, Paket-Upload-APIs) bleiben theoretische Exfil-Kanäle. Der
Proxy **begrenzt**, er eliminiert nicht. Gegenmittel: Liste eng halten + Logs auditieren
([`04-logging.md`](./04-logging.md)).

---

## 2. Produktwahl: Squid

§6.6 nennt Squid als Beispiel — hier die Begründung gegen Alternativen:

| Kandidat | Bewertung |
| -------- | --------- |
| **Squid** | ✅ **Gewählt.** Kampferprobt, `dstdomain`-ACLs, `CONNECT`-Filterung, optional **SNI-peek ohne Bump** (splice) für ehrliche HTTPS-Hostprüfung, `access.log` für Audit. Genau auf diesen Zweck zugeschnitten. |
| tinyproxy | Zu simpel: Filterung pro Ziel grob, kein SNI-peek. |
| Envoy | Mächtig, aber für reinen Allowlist-Egress overkill; mehr bewegliche Teile. |
| mitmproxy | Würde TLS aufbrechen (§3) — unnötiges Risiko & Wartung. |

Wie beim Warden gilt **Auditierbarkeit zuerst**: eine kleine, deklarative `squid.conf`
ist leichter zu prüfen als Eigenbau.

---

## 3. Grundsatzentscheidung: Kein TLS-MITM — SNI-peek + splice

**Kernentscheidung.** HTTPS-Verkehr wird **nicht entschlüsselt**. Stattdessen prüft Squid
beim TLS-Handshake den **SNI-Servernamen** (`ssl_bump peek` → `splice`) und filtert daran,
ohne ein eigenes CA-Zertifikat einzuschleusen.

**Warum so:**

- **Kein CA im Agent-Container** → keine neue Vertrauensbeziehung, kein Schlüssel, der zur
  Angriffsfläche wird. Passt zum §3-Axiom „nichts Geheimes in den Agenten".
- **Geringe Wartung & keine Brüche** bei Cert-Pinning (cargo, viele Tools pinnen).
- **Ausreichend für den Zweck:** Wir wollen Ziele *erlauben/sperren*, nicht Inhalte
  inspizieren.

**Warum nicht nur `CONNECT`-Host filtern?** Beim reinen `CONNECT host:443` könnte ein
böswilliger Client einen allowlisteten Host vortäuschen und danach zu einem anderen
Server sprechen (CONNECT-Host ≠ echtes SNI/Ziel). **SNI-peek** prüft den im Handshake
tatsächlich angefragten Namen → das Vortäuschen wird abgefangen. Plain-HTTP (`GET`) wird
ohnehin über `dstdomain` am Request-Host gefiltert.

⚠️ **Restgrenze, ehrlich:** SNI ist clientseitig gesetzt; gegen **ESNI/ECH** (verschlüsseltes
SNI) greift die Prüfung nicht mehr. Heute praktisch vernachlässigbar; bei Bedarf später
durch IP-Allowlist oder TLS-Bump nachschärfbar (Trade-off dokumentiert).

---

## 4. Detailfragen — Entscheidungen

✅ entschieden · ⚠️ entschieden mit Vorbehalt · ❓ braucht Bestätigung.

| # | Frage | Entscheidung |
| - | ----- | ------------ |
| Q1 | TLS aufbrechen (MITM)? | ✅ **Nein.** SNI-peek + splice, kein CA im Agenten (§3). |
| Q2 | CONNECT-Host vs. echtes SNI? | ✅ Per SNI-ACL gefiltert → CONNECT-Host-Spoofing abgefangen (§3). |
| Q3 | Subdomain-Wildcards? | ✅ `.domain`-Notation (Subdomains inkl.), exakte Hosts ohne Punkt ([`03-squid-config.md`](./03-squid-config.md)). |
| Q4 | GitHub voll oder read-only? | ⚠️ Start: `github.com` + Raw/Codeload. Bei Exfil-Sorge auf reine Read-Pfade einschränken (§6.6). |
| Q5 | Ports? | ✅ Nur 80/443 (`safe_ports`); CONNECT auf alles andere → deny. |
| Q6 | IP-Literale erlauben? | ✅ **Nein** — nur Domains in der Allowlist; IP-CONNECT trifft keine `dstdomain`-ACL → default-deny. |
| Q7 | Proxy-Auth (Agent→Proxy)? | ✅ **Keine** — wie bei Warden (W9.3): Netz ist die Grenze, Auth gäbe Scheinsicherheit. |
| Q8 | Caching? | ✅ Aus (`cache deny all`) — Filter+Audit, kein Cache-Poisoning/Disk-State. |
| Q9 | DNS-Exfil? | ✅ Strukturell zu: Agent hat keine eigene DNS-Route (`internal`-Netz), Squid resolved ([`02-network-isolation.md`](./02-network-isolation.md)). |
| Q10 | ESNI/ECH-Umgehung? | ⚠️ Heute vernachlässigbar; bei Bedarf IP-Allowlist/TLS-Bump nachrüsten (§3-Restgrenze). |

**Offene Punkte vor Inbetriebnahme:**

- ⚠️ **Transitive CDN-Hosts** je Paketmanager im konkreten Projekt verifizieren (ein
  fehlender CDN-Host = stiller Build-Bruch) — Allowlist gegen einen echten Clean-Build
  testen ([`03-squid-config.md`](./03-squid-config.md)).
- ⚠️ **`ssl_bump peek/splice`** setzt ein Squid-Build **mit** OpenSSL-Support voraus;
  Image-Variante prüfen (manche `squid:slim` ohne SSL-Support).
