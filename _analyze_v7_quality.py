import json
from pathlib import Path

OUTPUT = Path(__file__).parent / "output"

klang_with_rhymes = set()
entries = []

with open(OUTPUT / "sprachnudel_raw.snapshot.v7.merged.jsonl", "r", encoding="utf-8") as f:
    for line in f:
        e = json.loads(line.strip())
        entries.append(e)
        if e.get("count", 0) > 0:
            klang_with_rhymes.add(e.get("klang", "").casefold())

total = len(entries)
with_rhymes = sum(1 for e in entries if e.get("count", 0) > 0)
without_rhymes = total - with_rhymes

suspicious = 0
genuine_empty = 0
suspicious_examples = []

for e in entries:
    count = e.get("count", 0)
    if count == 0:
        klang = e.get("klang", "").casefold()
        if klang in klang_with_rhymes:
            suspicious += 1
            if len(suspicious_examples) < 40:
                suspicious_examples.append(f"  {e['suchwort']:30s} klang={klang:15s}")
        else:
            genuine_empty += 1

print(f"=== v7 Snapshot Gesamtanalyse ===")
print(f"Eintrage gesamt:     {total}")
print(f"Mit Reimen:          {with_rhymes} ({with_rhymes*100/total:.1f}%)")
print(f"Ohne Reime:          {without_rhymes} ({without_rhymes*100/total:.1f}%)")
print(f"")
print(f"Verdaechtig (count=0 aber klang existiert mit Reimen): {suspicious} ({suspicious*100/total:.1f}%)")
print(f"Echt leer (klang nirgends mit Reimen):                 {genuine_empty} ({genuine_empty*100/total:.1f}%)")
print(f"")
print(f"==> {suspicious} Eintrage muessen moeglicherweise nachgescraped werden!")
print(f"")
print(f"Beispiele verdächtig:")
for ex in suspicious_examples:
    print(ex)
