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

print("=== Merge zu v9 (v8 + Snowball Patch) ===\n")

print("Lade Quellen:")
v8_total, v8_rhymes = load_jsonl(OUTPUT / "sprachnudel_raw.snapshot.v8.merged.jsonl", "v8 Snapshot")
patch_total, patch_rhymes = load_jsonl(OUTPUT / "sprachnudel_snowball_patch.jsonl", "Snowball Patch")

total = len(entries)
with_rhymes = sum(1 for e in entries.values() if int(e.get("count", 0)) > 0)
with_kategorie = sum(1 for e in entries.values() if "kategorie" in e)
zero_count = total - with_rhymes

print(f"\nMerge-Ergebnis:")
print(f"  Gesamt: {total} Eintraege")
print(f"  Mit Reimen: {with_rhymes}")
print(f"  Ohne Reime (count=0): {zero_count}")
print(f"  Mit Kategorie: {with_kategorie}")

print(f"\nVergleich v8 -> v9:")
print(f"  v8 Gesamt: {v8_total}")
print(f"  Patch Eintraege: {patch_total}")
print(f"  v9 Gesamt: {total}")
print(f"  Neue Eintraege: {total - v8_total}")

v8_entries = {}
if (OUTPUT / "sprachnudel_raw.snapshot.v8.merged.jsonl").exists():
    with open(OUTPUT / "sprachnudel_raw.snapshot.v8.merged.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            key = e.get("suchwort", "").casefold()
            if key:
                v8_entries[key] = int(e.get("count", 0))

improved = 0
for key, e in entries.items():
    v8_count = v8_entries.get(key, 0)
    v9_count = int(e.get("count", 0))
    if v9_count > v8_count:
        improved += 1

print(f"  Verbesserte Eintraege (hoeherer count): {improved}")

out = OUTPUT / "sprachnudel_raw.snapshot.v9.merged.jsonl"
sorted_keys = sorted(entries.keys(), key=str.casefold)
with open(out, "w", encoding="utf-8") as f:
    for key in sorted_keys:
        f.write(json.dumps(entries[key], ensure_ascii=False) + "\n")

print(f"\nGespeichert: {out}")
print(f"Dateigroesse: {out.stat().st_size / 1024 / 1024:.1f} MB")
