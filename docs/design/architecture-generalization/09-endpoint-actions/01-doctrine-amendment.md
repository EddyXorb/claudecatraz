# 01 — Doktrin-Amendment: WHAT-vs-HOW-Linie in 08 §3.1 um `actions` erweitern

**Leitet ab aus** [`../09-endpoint-actions.md`](../09-endpoint-actions.md) §1.3 und §5
(„Doktrin-Amendment in 08 §3.1"). **Hängt ab von** — nichts (reine Doku).

> **Parallelisierbar:** Dies ist ein reiner Text-Edit an einem 08-Dokument, ohne
> Code-Abhängigkeit. Er kann gleichzeitig mit Schritt 01 (Action-Katalog) und vor
> allem anderen laufen. Eigener Commit.

## Ziel

08 §3.1 zieht heute die Linie **Identität/Scope kaskadiert nicht** vs. **Verhalten
(`rules`) kaskadiert**. 09 fügt eine dritte Kategorie ein: **host-unabhängiger Scope**
(`actions`), der *kaskadiert*. Das Amendment macht diese erweiterte Linie am
Ort der Original-Doktrin sichtbar, damit 08 und 09 nicht auseinanderlaufen.

## Umsetzung

1. Öffne
   [`../08-multi-target.md`](../08-multi-target.md) und finde §3.1 (die
   WHAT-vs-HOW- bzw. „kaskadiert / kaskadiert nicht"-Formulierung).
2. Ergänze die Doktrin um `actions` gemäß §1.3 des 09-Hauptdokuments. Zielaussage
   (wörtlich aus 09 §1.3, sinngemäß einpassen — nicht das 08-Dokument umschreiben,
   nur den einen Satz erweitern):

   > **Identität** (`host`, `type`) und **host-relativer Scope**
   > (`allowed_projects`) kaskadieren nicht; **host-unabhängiger Scope** (`actions`)
   > und **Verhalten** (`rules`) kaskadieren — per-Key-Merge, Listen ersetzen
   > komplett.

3. Setze einen kurzen Verweis auf `09-endpoint-actions.md` (§1.3), damit klar ist,
   woher die dritte Kategorie kommt. Keine weitergehende Umstrukturierung von 08.
4. Prüfe, ob §3.1 an anderer Stelle in 08 (z.B. §3.3 Kaskade) eine Auflistung der
   kaskadierenden Keys enthält, die `actions` konsistent mitnennen sollte; falls ja,
   dort denselben Zusatz. Nur, wenn es die Konsistenz erfordert — kein Wildwuchs.

## Nicht tun

- 08 **nicht** inhaltlich umbauen — nur die eine Doktrin-Linie um `actions` erweitern
  plus Querverweis.
- **Keine** Code-Änderung in diesem Schritt.
- `actions` nicht als `rules`-Unterpunkt darstellen — es ist eine **eigene**
  Kategorie neben `rules` (§1.3).

## Tests

Keine (reine Doku). Prüfe: Der Markdown-Link auf `09-endpoint-actions.md` stimmt, die
Formulierung widerspricht nicht 09 §1.3, und die §-Nummer in 08 ist korrekt.

## Verifikation

Keine Test-Suite. Sichtprüfung + Link-Check (relativer Pfad auflösbar).

## Commit

Als `EddyXorb`, kein Co-Author-Trailer. Nachricht:

```
docs(08): amend §3.1 cascade doctrine — actions cascade alongside rules
```

## Fertig-Kriterium

08 §3.1 nennt `actions` als kaskadierenden host-unabhängigen Scope neben `rules`, mit
Querverweis auf 09 §1.3; keine Widersprüche zwischen 08 und 09.
