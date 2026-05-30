import json

progress = set(json.load(open("output/sn_progress.json", encoding="utf-8")))

raw_woerter = set()
with open("output/sprachnudel_raw.jsonl", encoding="utf-8") as f:
    for line in f:
        if not line.strip():
            continue
        e = json.loads(line.strip())
        raw_woerter.add(e["suchwort"])

total_reime = 0
with open("output/sprachnudel_raw.jsonl", encoding="utf-8") as f:
    for line in f:
        if not line.strip():
            continue
        e = json.loads(line.strip())
        total_reime += len(e.get("results", [])) - 1  # minus suchwort entry

erfolgreich = raw_woerter
uebersprungen = progress - raw_woerter

print("=== SPRACHNUDEL SCRAPER STATUS ===")
print(f"Gesamt-Wörter:       26.605")
print(f"In Progress:         {len(progress)}")
print(f"Erfolgreich gescrapt: {len(erfolgreich)} ({len(erfolgreich)/26605*100:.1f}%)")
print(f"Übersprungen (500er): {len(uebersprungen)}")
print(f"Gesammelte Reime:     {total_reime:,}")
print()

if uebersprungen:
    print(f"Übersprungene Wörter ({len(uebersprungen)}):")
    for w in sorted(uebersprungen)[:30]:
        print(f"  - {w}")
    if len(uebersprungen) > 30:
        print(f"  ... und {len(uebersprungen)-30} mehr")

# Kann nicht "zuerst übersprungen dann erfolgreich" feststellen
# weil der Scraper keine History pro Wort speichert
print()
print("Hinweis: 'Übersprungen dann erfolgreich' kann nicht")
print("festgestellt werden — der Scraper speichert keine Retry-History.")
