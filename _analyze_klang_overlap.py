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

klang_with_rhymes = set()
v7 = {}
with open(OUTPUT / "sprachnudel_raw.snapshot.v7.merged.jsonl", "r", encoding="utf-8") as f:
    for line in f:
        e = json.loads(line.strip())
        sw = e.get("suchwort", "").casefold()
        v7[sw] = e
        if e.get("count", 0) > 0:
            klang_with_rhymes.add(e.get("klang", "").casefold())

print(f"Klang-Werte mit mind. 1 Reim: {len(klang_with_rhymes)}")

suspicious = []
genuine_empty = []
missing = []

for key, info in words.items():
    if key in v7:
        entry = v7[key]
        count = entry.get("count", 0)
        klang = entry.get("klang", "").casefold()
        if count == 0:
            if klang in klang_with_rhymes:
                suspicious.append({
                    "wort": info["wort"],
                    "kategorie": info["kategorie"],
                    "klang": klang,
                    "grund": "klang_existiert_mit_reimen"
                })
            else:
                genuine_empty.append({
                    "wort": info["wort"],
                    "kategorie": info["kategorie"],
                    "klang": klang,
                    "grund": "klang_unbekannt"
                })
    else:
        missing.append({
            "wort": info["wort"],
            "kategorie": info["kategorie"],
            "klang": "NICHT IN v7",
            "grund": "fehlen_im_snapshot"
        })

print(f"\nVerdaechtig (klang hat Reime bei anderen Woertern): {len(suspicious)}")
print(f"Echt leer (klang hat nirgends Reime):               {len(genuine_empty)}")
print(f"Ganz fehlend:                                        {len(missing)}")

to_rescrape = suspicious + missing
print(f"\n==> Zum Nachscrapen: {len(to_rescrape)} Woerter")

out = OUTPUT / "rescrape_bauernspruch_v2.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump(to_rescrape, f, ensure_ascii=False, indent=2)
print(f"Gespeichert: {out}")

print(f"\nBeispiele verdächtig:")
for item in sorted(suspicious, key=lambda x: x["wort"].casefold())[:30]:
    print(f"  {item['wort']:30s} klang={item['klang']:15s} | {item['kategorie']}")
