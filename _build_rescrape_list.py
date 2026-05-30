import json
import re
from pathlib import Path

OUTPUT = Path(__file__).parent / "output"

klang_with_rhymes = set()
entries = []
bauernspruch_words = {}

md = (OUTPUT / "bauernspruch_wortliste_v3_modern.md").read_text(encoding="utf-8")
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
        if key not in bauernspruch_words:
            bauernspruch_words[key] = current_cat

with open(OUTPUT / "sprachnudel_raw.snapshot.v7.merged.jsonl", "r", encoding="utf-8") as f:
    for line in f:
        e = json.loads(line.strip())
        entries.append(e)
        if e.get("count", 0) > 0:
            klang_with_rhymes.add(e.get("klang", "").casefold())

rescrape_list = []
for e in entries:
    count = e.get("count", 0)
    if count > 0:
        continue
    klang = e.get("klang", "").casefold()
    if klang not in klang_with_rhymes:
        continue
    sw = e.get("suchwort", "")
    item = {"wort": sw, "klang": klang}
    key = sw.casefold()
    if key in bauernspruch_words:
        item["kategorie"] = bauernspruch_words[key]
    rescrape_list.append(item)

out = OUTPUT / "rescrape_v1_full.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump(rescrape_list, f, ensure_ascii=False, indent=2)

print(f"Rescrape-Liste: {len(rescrape_list)} Woerter")
print(f"  Davon Bauernspruch: {sum(1 for x in rescrape_list if 'kategorie' in x)}")
print(f"Gespeichert: {out}")
