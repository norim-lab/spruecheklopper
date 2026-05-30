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

print(f"Gesamt: {len(words)} Woerter in {len(set(v['kategorie'] for v in words.values()))} Kategorien")
for cat in sorted(set(v['kategorie'] for v in words.values())):
    n = sum(1 for v in words.values() if v['kategorie'] == cat)
    print(f"  {cat}: {n}")

existing = set()
snapshot = OUTPUT / "sprachnudel_raw.snapshot.v5.merged.jsonl"
if snapshot.exists():
    with open(snapshot, "r", encoding="utf-8") as f:
        for line in f:
            e = json.loads(line.strip())
            existing.add(e.get("suchwort", "").casefold())
    print(f"\nSnapshot-Einträge: {len(existing)}")

derewo_patch = OUTPUT / "sprachnudel_derewo_patch.jsonl"
if derewo_patch.exists():
    dp_count = 0
    with open(derewo_patch, "r", encoding="utf-8") as f:
        for line in f:
            e = json.loads(line.strip())
            key = e.get("suchwort", "").casefold()
            existing.add(key)
            dp_count += 1
    print(f"Derewo-Patch-Einträge: {dp_count}")

missing = {k: v for k, v in words.items() if k not in existing}
print(f"\nBereits abgedeckt: {len(words) - len(missing)}")
print(f"Noch zu scrapen: {len(missing)}")

out_file = OUTPUT / "missing_bauernspruch_words.json"
with open(out_file, "w", encoding="utf-8") as f:
    json.dump(missing, f, ensure_ascii=False, indent=2)
print(f"\nGespeichert: {out_file}")
