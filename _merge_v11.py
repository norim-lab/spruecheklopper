import json
from pathlib import Path

OUTPUT = Path(__file__).parent / "output"

entries = {}

def load_jsonl(path: Path, label: str):
    if not path.exists():
        print(f"  {label}: nicht gefunden, uebersprungen")
        return 0, 0
    count = 0
    with_rhymes = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            key = e.get("suchwort", "").casefold()
            if not key:
                continue
            count += 1
            if int(e.get("count", 0)) > 0:
                with_rhymes += 1
            existing = entries.get(key)
            if existing is None:
                entries[key] = e
            else:
                existing_count = int(existing.get("count", 0))
                new_count = int(e.get("count", 0))
                if new_count > existing_count:
                    if "kategorie" in existing and "kategorie" not in e:
                        e["kategorie"] = existing["kategorie"]
                    entries[key] = e
                elif "kategorie" in e and "kategorie" not in existing:
                    existing["kategorie"] = e["kategorie"]
    print(f"  {label}: {count} Eintraege ({with_rhymes} mit Reimen)")
    return count, with_rhymes

print("=== Merge zu v11 (v10 + alle neuen Patches) ===\n")

print("Lade Quellen:")
v10_total, v10_rhymes = load_jsonl(OUTPUT / "sprachnudel_raw.snapshot.v10.merged.jsonl", "v10 Snapshot")

patches = [
    ("sprachnudel_countzero_patch.jsonl", "CountZero Patch"),
    ("sprachnudel_rescrape_v2_patch.jsonl", "Rescrape v2 Patch"),
    ("sprachnudel_snowball_patch.jsonl", "Snowball Patch"),
    ("sprachnudel_derewo_patch.jsonl", "DeReWo Patch"),
    ("sprachnudel_complete_patch.jsonl", "Complete Patch"),
    ("sprachnudel_bauernspruch_patch.jsonl", "Bauernspruch Patch"),
    ("sprachnudel_sitemap_patch.jsonl", "Sitemap Patch"),
    ("sprachnudel_rescrape_patch.jsonl", "Rescrape v1 Patch"),
    ("sprachnudel_targeted_patch.jsonl", "Targeted Patch"),
]

for filename, label in patches:
    load_jsonl(OUTPUT / filename, label)

total = len(entries)
with_rhymes = sum(1 for e in entries.values() if int(e.get("count", 0)) > 0)
with_kategorie = sum(1 for e in entries.values() if "kategorie" in e)
zero_count = total - with_rhymes

print(f"\nMerge-Ergebnis:")
print(f"  Gesamt: {total} Eintraege")
print(f"  Mit Reimen: {with_rhymes}")
print(f"  Ohne Reime (count=0): {zero_count}")
print(f"  Mit Kategorie: {with_kategorie}")

print(f"\nVergleich v10 -> v11:")
print(f"  v10 Gesamt: {v10_total}")
print(f"  v11 Gesamt: {total}")
print(f"  Neue Eintraege: {total - v10_total}")

v10_entries = {}
if (OUTPUT / "sprachnudel_raw.snapshot.v10.merged.jsonl").exists():
    with open(OUTPUT / "sprachnudel_raw.snapshot.v10.merged.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            key = e.get("suchwort", "").casefold()
            if key:
                v10_entries[key] = int(e.get("count", 0))

new_words = 0
improved = 0
for key, e in entries.items():
    if key not in v10_entries:
        new_words += 1
    else:
        v10_count = v10_entries[key]
        v11_count = int(e.get("count", 0))
        if v11_count > v10_count:
            improved += 1

print(f"  Neue Woerter: {new_words}")
print(f"  Verbesserte Eintraege (hoeherer count): {improved}")

out = OUTPUT / "sprachnudel_raw.snapshot.v11.merged.jsonl"
sorted_keys = sorted(entries.keys(), key=str.casefold)
with open(out, "w", encoding="utf-8") as f:
    for key in sorted_keys:
        f.write(json.dumps(entries[key], ensure_ascii=False) + "\n")

print(f"\nGespeichert: {out}")
print(f"Dateigroesse: {out.stat().st_size / 1024 / 1024:.1f} MB")
