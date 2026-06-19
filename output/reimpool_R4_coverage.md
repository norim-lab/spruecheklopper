# R.4-data-1 Coverage: gelaeufige Reimpartner (<= 8)

## VORARBEIT

- Reimwoerter im Generator kommen aus `output/reimgruppen_derb.jsonl` (kuratierte Gruppen) und Seeds aus `output/seed_woerter_v22.json`.
- Die Wort-Haeufigkeit steht als Integer-Feld `haeufigkeit` (1=sehr haeufig/alltaeglich ... 100=selten/exotisch).
- Gate: fuer diesen Build gilt haeufigkeit<=8; Worte ohne Haeufigkeit gelten als exotisch und sind ausgeschlossen.
- Korpus-Endungsfamilien: aus `output/sprueche.db` (veroeffentlicht=1; Fallback: Top-250 nach judge_score), Endung ueber generator._reim_endung(Zeilenendwort/Reimwort).
- Neue Kandidaten werden aus `output/derewo_freq_cache.json` (DeReWo-Cache) gezogen, ebenfalls ueber Endung gruppiert.

Korpus: 233 Sprueche, 141 Endungsfamilien.

## Tabelle

| Endungsfamilie | Korpus-Treffer | Partner<=8 VORHER | Partner<=8 NACHHER | Neu hinzugefuegt | <12 nachher? |
| --- | --- | --- | --- | --- | --- |
| arsch | 44 | 0 | 0 | 0 | ja |
| at | 25 | 0 | 1 | 1 | ja |
| itz | 19 | 0 | 0 | 0 | ja |
| is | 18 | 0 | 1 | 1 | ja |
| och | 16 | 0 | 4 | 4 | ja |
| an | 15 | 0 | 4 | 4 | ja |
| aft | 14 | 0 | 1 | 1 | ja |
| al | 14 | 0 | 4 | 4 | ja |
| eck | 14 | 0 | 0 | 0 | ja |
| opf | 14 | 0 | 0 | 0 | ja |
| e | 13 | 0 | 12 | 12 | nein |
| el | 13 | 1 | 5 | 4 | ja |
| und | 13 | 1 | 3 | 2 | ja |
| ank | 12 | 0 | 0 | 0 | ja |
| er | 12 | 1 | 12 | 11 | nein |
| acht | 11 | 0 | 0 | 0 | ja |
| umpf | 11 | 0 | 0 | 0 | ja |
| aps | 10 | 0 | 0 | 0 | ja |
| on | 10 | 0 | 4 | 4 | ja |
| af | 9 | 0 | 0 | 0 | ja |
| am | 9 | 0 | 3 | 3 | ja |
| ekt | 9 | 0 | 0 | 0 | ja |
| erk | 9 | 0 | 0 | 0 | ja |
| es | 9 | 0 | 1 | 1 | ja |
| et | 9 | 0 | 1 | 1 | ja |
| ind | 9 | 0 | 1 | 1 | ja |
| as | 8 | 0 | 3 | 3 | ja |
| ert | 8 | 0 | 0 | 0 | ja |
| icht | 8 | 0 | 1 | 1 | ja |
| in | 8 | 0 | 6 | 6 | ja |
| ing | 8 | 0 | 0 | 0 | ja |
| ink | 8 | 0 | 0 | 0 | ja |
| os | 8 | 0 | 1 | 1 | ja |
| urz | 8 | 0 | 1 | 1 | ja |
| arm | 7 | 0 | 0 | 0 | ja |
| art | 7 | 0 | 0 | 0 | ja |
| echt | 7 | 0 | 0 | 0 | ja |
| ild | 7 | 0 | 1 | 1 | ja |
| or | 7 | 0 | 1 | 1 | ja |
| ut | 7 | 0 | 1 | 1 | ja |
| alt | 6 | 0 | 1 | 1 | ja |
| atz | 6 | 0 | 1 | 1 | ja |
| egt | 6 | 0 | 0 | 0 | ja |
| enk | 6 | 0 | 0 | 0 | ja |
| orm | 6 | 0 | 0 | 0 | ja |
| ort | 6 | 0 | 2 | 2 | ja |
| achs | 5 | 0 | 0 | 0 | ja |
| ackt | 5 | 0 | 0 | 0 | ja |
| akt | 5 | 0 | 0 | 0 | ja |
| ent | 5 | 1 | 2 | 1 | ja |
| i | 5 | 4 | 8 | 4 | ja |
| us | 5 | 0 | 2 | 2 | ja |
| acks | 4 | 0 | 0 | 0 | ja |
| alm | 4 | 0 | 0 | 0 | ja |
| alz | 4 | 0 | 0 | 0 | ja |
| amt | 4 | 0 | 1 | 1 | ja |
| and | 4 | 0 | 1 | 1 | ja |
| asch | 4 | 0 | 0 | 0 | ja |
| esch | 4 | 0 | 0 | 0 | ja |
| ick | 4 | 0 | 0 | 0 | ja |
| ips | 4 | 0 | 0 | 0 | ja |
| isch | 4 | 0 | 1 | 1 | ja |
| ist | 4 | 0 | 0 | 0 | ja |
| ol | 4 | 0 | 0 | 0 | ja |
| ur | 4 | 0 | 2 | 2 | ja |
| usch | 4 | 0 | 0 | 0 | ja |
| ack | 3 | 0 | 0 | 0 | ja |
| ark | 3 | 1 | 2 | 1 | ja |
| elt | 3 | 1 | 1 | 0 | ja |
| enz | 3 | 0 | 0 | 0 | ja |
| erg | 3 | 0 | 0 | 0 | ja |
| erz | 3 | 0 | 1 | 1 | ja |
| est | 3 | 0 | 0 | 0 | ja |
| ickt | 3 | 0 | 0 | 0 | ja |
| il | 3 | 0 | 2 | 2 | ja |
| int | 3 | 0 | 0 | 0 | ja |
| intz | 3 | 0 | 0 | 0 | ja |
| inz | 3 | 0 | 0 | 0 | ja |
| ock | 3 | 0 | 0 | 0 | ja |
| ohr | 3 | 0 | 0 | 0 | ja |
| ont | 3 | 0 | 0 | 0 | ja |
| un | 3 | 0 | 2 | 2 | ja |
| ald | 2 | 0 | 0 | 0 | ja |
| ang | 2 | 0 | 1 | 1 | ja |
| ax | 2 | 0 | 0 | 0 | ja |
| eckt | 2 | 0 | 0 | 0 | ja |
| ehr | 2 | 0 | 2 | 2 | ja |
| ehrt | 2 | 0 | 0 | 0 | ja |
| eld | 2 | 1 | 1 | 0 | ja |
| eln | 2 | 0 | 0 | 0 | ja |
| ep | 2 | 0 | 0 | 0 | ja |
| ern | 2 | 1 | 3 | 2 | ja |
| ers | 2 | 0 | 1 | 1 | ja |
| ich | 2 | 0 | 5 | 5 | ja |
| id | 2 | 0 | 0 | 0 | ja |
| if | 2 | 0 | 0 | 0 | ja |
| ift | 2 | 0 | 0 | 0 | ja |
| ilt | 2 | 0 | 0 | 0 | ja |
| it | 2 | 0 | 7 | 7 | ja |
| of | 2 | 0 | 0 | 0 | ja |
| ohl | 2 | 0 | 1 | 1 | ja |
| olz | 2 | 0 | 0 | 0 | ja |
| orf | 2 | 0 | 0 | 0 | ja |
| ost | 2 | 0 | 0 | 0 | ja |
| ot | 2 | 0 | 0 | 0 | ja |
| uch | 2 | 0 | 1 | 1 | ja |
| uf | 2 | 0 | 2 | 2 | ja |
| ukt | 2 | 0 | 0 | 0 | ja |
| a | 1 | 0 | 4 | 4 | ja |
| ab | 1 | 0 | 1 | 1 | ja |
| act | 1 | 0 | 0 | 0 | ja |
| adt | 1 | 0 | 1 | 1 | ja |
| ahm | 1 | 0 | 0 | 0 | ja |
| ahn | 1 | 0 | 0 | 0 | ja |
| ahr | 1 | 0 | 1 | 1 | ja |
| ahrt | 1 | 0 | 0 | 0 | ja |
| aks | 1 | 0 | 0 | 0 | ja |
| ar | 1 | 2 | 4 | 2 | ja |
| arg | 1 | 0 | 0 | 0 | ja |
| av | 1 | 0 | 0 | 0 | ja |
| eb | 1 | 0 | 0 | 0 | ja |
| ect | 1 | 0 | 0 | 0 | ja |
| ed | 1 | 0 | 1 | 1 | ja |
| ehl | 1 | 0 | 0 | 0 | ja |
| eht | 1 | 0 | 0 | 0 | ja |
| en | 1 | 0 | 12 | 12 | nein |
| erd | 1 | 0 | 0 | 0 | ja |
| ih | 1 | 0 | 0 | 0 | ja |
| ikt | 1 | 0 | 0 | 0 | ja |
| ings | 1 | 0 | 1 | 1 | ja |
| isl | 1 | 0 | 0 | 0 | ja |
| ocks | 1 | 0 | 0 | 0 | ja |
| ond | 1 | 0 | 0 | 0 | ja |
| op | 1 | 0 | 0 | 0 | ja |
| osch | 1 | 0 | 0 | 0 | ja |
| own | 1 | 0 | 0 | 0 | ja |
| ox | 1 | 0 | 0 | 0 | ja |
| unt | 1 | 0 | 0 | 0 | ja |
| urd | 1 | 0 | 0 | 0 | ja |
| urt | 1 | 0 | 0 | 0 | ja |
| y | 1 | 0 | 0 | 0 | ja |

Familien < 12 gelaeufige Partner: VORHER 141 -> NACHHER 138
