# R.1b Diagnose-Report (Hebel-Pruefung)

Quelle: output/ab_R1b_raw.json (R.1b-Lauf).

**Kopf-Verdikt (Hebel aktiv?)**
- Prompt-Echo belegbar? ❌ (Prompt-Text wurde nicht mitgeloggt -> Arm-Zuordnung nicht beweisbar)
- Haeufigkeits-Cap (MAX_HAEUFIGKEIT=5) wirksam in B/C? ❌ (Verstoesse: 16, unbekannte DB-Treffer: 1)
- Sex-Cap ausgefuehrt? ✅ (Zellen mit Kandidaten-Pool: 17/18, geblockte Kandidaten gesamt: 0)
- Lauf insgesamt: **kaputt** (Zellen mit Fehler oder leerem Output: 1/18)

## 1) PROMPT-ECHO (was ging real an grok?)

In `ab_R1b_raw.json` wird der tatsaechlich gesendete System-Prompt nicht gespeichert.
Damit laesst sich NICHT beweisen, ob Arm B/C wirklich den V4-Prompt (inkl. FEWSHOT_V4) genutzt hat.

| Arm | Seed | Erwarteter Prompt | Prompt-Text geloggt? | FEWSHOT_V4 enthalten? | Fewshot geloggt? | Fehler (kurz) |
| --- | --- | --- | --- | --- | --- | --- |
| A | Geld | SYSTEM_PROMPT | nein | unbekannt | nein |  |
| B | Geld | SYSTEM_PROMPT_V4_ZWEI | nein | unbekannt | nein |  |
| C | Geld | SYSTEM_PROMPT_V4_VIER | nein | unbekannt | nein |  |
| A | Land | SYSTEM_PROMPT | nein | unbekannt | nein |  |
| B | Land | SYSTEM_PROMPT_V4_ZWEI | nein | unbekannt | nein |  |
| C | Land | SYSTEM_PROMPT_V4_VIER | nein | unbekannt | nein |  |
| A | Konflikt | SYSTEM_PROMPT | nein | unbekannt | nein |  |
| B | Konflikt | SYSTEM_PROMPT_V4_ZWEI | nein | unbekannt | nein |  |
| C | Konflikt | SYSTEM_PROMPT_V4_VIER | nein | unbekannt | nein |  |
| A | Zweck | SYSTEM_PROMPT | nein | unbekannt | nein |  |
| B | Zweck | SYSTEM_PROMPT_V4_ZWEI | nein | unbekannt | nein |  |
| C | Zweck | SYSTEM_PROMPT_V4_VIER | nein | unbekannt | nein |  |
| A | Geschenk | SYSTEM_PROMPT | nein | unbekannt | nein |  |
| B | Geschenk | SYSTEM_PROMPT_V4_ZWEI | nein | unbekannt | nein |  |
| C | Geschenk | SYSTEM_PROMPT_V4_VIER | nein | unbekannt | nein | Exception: 'list' object has no attribute 'replace' |
| A | Tun | SYSTEM_PROMPT | nein | unbekannt | nein |  |
| B | Tun | SYSTEM_PROMPT_V4_ZWEI | nein | unbekannt | nein |  |
| C | Tun | SYSTEM_PROMPT_V4_VIER | nein | unbekannt | nein |  |

Befund: Ohne Prompt-Log sind wir bei der Kernfrage (Prompt aktiv?) technisch blind.

## 2) HAEUFIGKEITS-CAP (MAX_HAEUFIGKEIT=5 in B/C)

| Arm | Seed | Paar | Reimwort 1 | h1 | Reimwort 2 | h2 | Cap | >Cap? |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A | Geld | Z1/Z2 | Tor | 9 | Ohr | 11 | 18 | nein |
| B | Geld | Z1/Z2 | befüllt | 100 | Rumpf | 14 | 5 | ja |
| C | Geld | Z1/Z2 | abdominal | 21 | zervikal | 22 | 5 | ja |
| C | Geld | Z3/Z4 | Ding | 10 | String | 16 | 5 | ja |
| A | Land | Z1/Z2 | Barsch | 17 | Arsch | 16 | 18 | nein |
| B | Land | Z1/Z2 | Barsch | 17 | Arsch | 16 | 5 | ja |
| C | Land | Z1/Z2 | Ross | 13 | Schoss | 14 | 5 | ja |
| C | Land | Z3/Z4 | enorm | 11 | Norm | 13 | 5 | ja |
| A | Konflikt | Z1/Z2 | Schurz | 18 | Furz | 19 | 18 | ja |
| B | Konflikt | Z1/Z2 | erpicht | 18 | Gewicht | 12 | 5 | ja |
| C | Konflikt | Z1/Z2 | erregt | 15 | bewegt | 12 | 5 | ja |
| C | Konflikt | Z3/Z4 | kreuzfidel | 21 | parallel | 12 | 5 | ja |
| A | Zweck | Z1/Z2 | Fass | 13 | triefnass | 22 | 18 | ja |
| B | Zweck | Z1/Z2 | dreht | (?) | Fut | 20 | 5 | ja |
| C | Zweck | Z1/Z2 | Stirn | 13 | Zwirn | 17 | 5 | ja |
| C | Zweck | Z3/Z4 | sensuell | 21 | kommerziell | 13 | 5 | ja |
| A | Geschenk | Z1/Z2 | Marsch | 13 | Arsch | 16 | 18 | nein |
| B | Geschenk | Z1/Z2 | Barsch | 17 | Arsch | 16 | 5 | ja |
| C | Geschenk | Z1/Z2 | (fehlend) | (?) | (fehlend) | (?) | 5 | nein |
| C | Geschenk | Z3/Z4 | (fehlend) | (?) | (fehlend) | (?) | 5 | nein |
| A | Tun | Z1/Z2 | Niss | 21 | Beschiss | 21 | 18 | ja |
| B | Tun | Z1/Z2 | Schwank | 15 | blitzblank | 18 | 5 | ja |
| C | Tun | Z1/Z2 | spitz | 13 | Ritz | 16 | 5 | ja |
| C | Tun | Z3/Z4 | Mund | 12 | Schlund | 17 | 5 | ja |

Explizit: abdominal/zervikal

| Arm | Seed | Wort | haeufigkeit | >5 (nur B/C)? |
| --- | --- | --- | --- | --- |
| C | Geld | abdominal | 21 | ja |
| C | Geld | zervikal | 22 | ja |

## 3) SEX-CAP (Blockliste + Trigger)

| Arm | Seed | Pool n_total | Pool n_blocked | sex_capped (alle geblockt)? | Schlusswort | Schlusswort in Blockliste? |
| --- | --- | --- | --- | --- | --- | --- |
| A | Geld | 5 | 0 | nein | ohr | nein |
| B | Geld | 8 | 0 | nein | rumpf | nein |
| C | Geld | 3 | 0 | nein | string | nein |
| A | Land | 6 | 0 | nein | arsch | nein |
| B | Land | 8 | 0 | nein | arsch | nein |
| C | Land | 5 | 0 | nein | norm | nein |
| A | Konflikt | 6 | 0 | nein | furz | nein |
| B | Konflikt | 8 | 0 | nein | gewicht | nein |
| C | Konflikt | 5 | 0 | nein | parallel | nein |
| A | Zweck | 7 | 0 | nein | triefnass | nein |
| B | Zweck | 8 | 0 | nein | fut | nein |
| C | Zweck | 3 | 0 | nein | kommerziell | nein |
| A | Geschenk | 7 | 0 | nein | arsch | nein |
| B | Geschenk | 8 | 0 | nein | arsch | nein |
| C | Geschenk | 0 | 0 | nein | (leer) | nein |
| A | Tun | 6 | 0 | nein | beschiss | nein |
| B | Tun | 8 | 0 | nein | blitzblank | nein |
| C | Tun | 4 | 0 | nein | schlund | nein |

Problemwoerter (aus User-Liste) im Sieger-Output:

| Wort | als Schlusswort (Anzahl) | irgendwo im Spruch (Anzahl) | in Blockliste? |
| --- | --- | --- | --- |
| fut | 1 | 1 | nein |
| arsch | 4 | 4 | nein |
| rumpf | 1 | 1 | nein |
| gaul | 0 | 1 | nein |

Fehlende Blocklisten-Woerter (wurden als Schlusswort ausgegeben, sind aber NICHT geblockt):

- fut, arsch, rumpf

Hinweis: Der Sex-Cap im Harness prueft nur das **allerletzte Wort** (Schlusswort), nicht beliebige Woerter im Spruchkoerper.

## 4) Geschenk·C (kein Output) — konkrete Ursache

- error: `Exception: 'list' object has no attribute 'replace'`
- judge_pool: 0 Kandidaten
- sex_cap_stats.n_total: 0
- vierzeiler_validation: active=True, n_rejected=0

## Was war kaputt + minimaler Fix (nur benennen)

- PROMPT-ECHO: `ab_R1b_raw.json` loggt den gesendeten Prompt nicht -> minimal: pro (Arm,Seed) `system_prompt_name` + `system_prompt_sha256` (oder erstes N Zeichen) mitschreiben.
- GESCHENK·C: Exception (`list` hat kein `.replace`) -> minimal: Traceback/Stacktrace in raw loggen, damit die Ursache lokalisierbar ist (aktuell nur Kurzstring).
- SEX-CAP: technisch ausgefuehrt, aber Blockliste deckt 'arsch'/'fut' nicht ab -> falls gewuenscht: diese Schlusswoerter explizit aufnehmen.
- ROBUSTHEIT: bei Exceptions ist `ok` aktuell irrefuehrend (leerer Output wird als ok=true markiert) -> minimal: ok-Flag strikt aus `best.ok` ableiten.
