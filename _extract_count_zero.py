import json
from pathlib import Path

v9 = Path("output/sprachnudel_raw.snapshot.v9.merged.jsonl")

words = []
with open(v9, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        e = json.loads(line)
        if int(e.get("count", 0)) == 0:
            words.append({"wort": e.get("suchwort", "")})

print(f"Count=0 Wörter: {len(words)}")

out = Path("output/count_zero_words.json")
with open(out, "w", encoding="utf-8") as f:
    json.dump(words, f, ensure_ascii=False)
print(f"Gespeichert: {out}")
