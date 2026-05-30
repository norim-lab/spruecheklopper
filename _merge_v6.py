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

print("=== Merge zu v7 (FINAL) ===\n")

print("Lade Quellen:")
load_jsonl(OUTPUT / "sprachnudel_raw.snapshot.v6.merged.jsonl", "v6 Snapshot")
load_jsonl(OUTPUT / "sprachnudel_snowball_patch.jsonl", "Schneeball Patch")

total = len(entries)
with_rhymes = sum(1 for e in entries.values() if int(e.get("count", 0)) > 0)
with_kategorie = sum(1 for e in entries.values() if "kategorie" in e)

print(f"\nMerge-Ergebnis:")
print(f"  Gesamt: {total} Eintraege")
print(f"  Mit Reimen: {with_rhymes}")
print(f"  Ohne Reime: {total - with_rhymes}")
print(f"  Mit Kategorie: {with_kategorie}")

out = OUTPUT / "sprachnudel_raw.snapshot.v7.merged.jsonl"
sorted_keys = sorted(entries.keys(), key=str.casefold)
with open(out, "w", encoding="utf-8") as f:
    for key in sorted_keys:
        f.write(json.dumps(entries[key], ensure_ascii=False) + "\n")

print(f"\nGespeichert: {out}")
print(f"Dateigroesse: {out.stat().st_size / 1024 / 1024:.1f} MB")
