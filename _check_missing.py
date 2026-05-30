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

print(f"MD-Datei: {len(words)} Woerter")

suchwort_set = set()
for snap in ["sprachnudel_raw.snapshot.v7.merged.jsonl"]:
    p = OUTPUT / snap
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                e = json.loads(line.strip())
                suchwort_set.add(e.get("suchwort", "").casefold())
print(f"v7 Suchworter: {len(suchwort_set)}")

for patch in ["sprachnudel_derewo_patch.jsonl", "sprachnudel_bauernspruch_patch.jsonl", "sprachnudel_snowball_patch.jsonl"]:
    p = OUTPUT / patch
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                e = json.loads(line.strip())
                suchwort_set.add(e.get("suchwort", "").casefold())
print(f"Total Suchworter (inkl. Patches): {len(suchwort_set)}")

really_missing = {k: v for k, v in words.items() if k not in suchwort_set}
print(f"\nWirklich fehlend: {len(really_missing)}")

for k, v in sorted(really_missing.items(), key=lambda x: x[1]["kategorie"]):
    print(f"  {v['wort']} ({v['kategorie']})")

out = OUTPUT / "missing_bauernspruch_v2.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump(really_missing, f, ensure_ascii=False, indent=2)
print(f"\nGespeichert: {out}")
