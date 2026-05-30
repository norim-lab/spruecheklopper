import json
import re
from pathlib import Path

OUTPUT = Path(__file__).parent / "output"

md = (OUTPUT / "bauernspruch_wortliste_v3_modern.md").read_text(encoding="utf-8")
words = {}
current_cat = ""

for line in md.splitlines():
    m = re.match(r"^##\s+\d+\.\s+(.+)", line)
    if m:
        current_cat = m.group(1).strip()
        continue
    if not current_cat:
        continue
    if line.startswith("#") or line.startswith("---") or line.startswith("*") or line.startswith("\u2713"):
        continue
    if line.startswith("**"):
        continue
    for raw in line.split(","):
        w = raw.strip(" ,.*()[]{}\"")
        w = re.sub(r"\s*\(.*?\)\s*$", "", w)
        if not w or len(w) < 2:
            continue
        if w.startswith("#") or w.startswith("-") or w.startswith("|"):
            continue
        if not (re.match(r"^[A-Za-z\u00c4\u00d6\u00dc\u00e4\u00f6\u00fc\u00df]", w)):
            continue
        key = w.casefold()
        if key not in words:
            words[key] = {"wort": w, "kategorie": current_cat}

print(f"Bauernspruch-Worter gesamt: {len(words)}")

v7 = {}
with open(OUTPUT / "sprachnudel_raw.snapshot.v7.merged.jsonl", "r", encoding="utf-8") as f:
    for line in f:
        e = json.loads(line.strip())
        sw = e.get("suchwort", "").casefold()
        v7[sw] = e

print(f"v7 Eintrage: {len(v7)}")

with_rhyme = 0
without_rhyme = 0
missing = 0
empty_list = []

for key, info in words.items():
    if key in v7:
        entry = v7[key]
        count = entry.get("count", 0)
        if count > 0:
            with_rhyme += 1
        else:
            without_rhyme += 1
            empty_list.append((info["wort"], info["kategorie"], entry.get("klang", "?")))
    else:
        missing += 1
        empty_list.append((info["wort"], info["kategorie"], "NICHT IN v7"))

print(f"\nMit Reimen: {with_rhyme}")
print(f"Ohne Reime (count=0): {without_rhyme}")
print(f"Ganz fehlend: {missing}")

print(f"\n--- Worter mit count=0 oder fehlend ---")
for w, cat, klang in sorted(empty_list, key=lambda x: x[0].casefold()):
    print(f"  {w:30s} | {klang:15s} | {cat}")

rescrape_words = []
for w, cat, klang in empty_list:
    rescrape_words.append({"wort": w, "kategorie": cat})

out = OUTPUT / "rescrape_bauernspruch_empty.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump(rescrape_words, f, ensure_ascii=False, indent=2)
print(f"\nRescrape-Liste gespeichert: {out} ({len(rescrape_words)} Worter)")
