# Kuratierte Reim-Derivate (J.2)

Dieses Verzeichnis enthält die finalen, aufbereiteten Reimgruppen für den Bauernspruch-Generator. Die Rohdaten (424 MB) sind **nicht im Repo** gespeichert, da sie zu gross sind.


## Kuratierte Dateien (im Repo)
- **`output/reimgruppen_derb.jsonl`**: 177 Gruppen von Reimwörtern (jeweils ein JSON-Zeile), gefiltert nach:
  - Haeufigkeit <= 30
  - Silben: 1–4
  - Wortart: Substantiv, Verb, Adjektiv
  - **Fremdwort-/Exotik-Filter (J.2)**: Endungen `-ist`, `-isten`, `-ismus`, `-morph`, `-phyll`, `-kurs`, `-vikt`, `-itis`, Grammatik-Begriffe (`verb`, `adverb`, `biderb`, `superb`), > 13 Zeichen, > 4 Silben, Eigennamen mit Ziffern. Ausserdem Whitelist für geläufige Ausnahmen (`Station`, `Nation`, `Pension`, `Kind`, `Hand`, `Christ`, `Frist`, `List`, `Mist`, `Tourist`, `Pianist`, `Skikurs`, `Tanzkurs`, `Sprachkurs`, `Yogakurs`, `Kochkurs`, `Crashkurs`, `Crash‑Kurs`, `Schnupperkurs`, `Fortbildungskurs`, `Einsteigerkurs`, `Tourismus`, `Organismus`).
  - Jede Gruppe hat ≥ 4 Partnerwörter.
- **`output/seed_woerter_v22.json`**: 177 Seeds, sortiert nach Haeufigkeit.


## Regenerierung der Dateien
Um die Dateien neu zu bauen, brauchst Du das **v12‑Export der Rohdaten** (Dateiname: `sprachnudel_export.v12.json` im Repo‑Root). Dann:
```bash
python tools/build_reimgruppen.py
```


## Hinweis
Die Rohdatei (`sprachnudel_export.v12.json`) und SQLite‑Datenbanken sind **aus dem Repo ausgeschlossen** (siehe [`.gitignore`](../.gitignore)), da sie zu gross sind.
