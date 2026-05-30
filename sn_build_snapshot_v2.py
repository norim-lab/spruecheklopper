import json
from pathlib import Path


BASE_DIR = Path(r"C:\Users\miron\Documents\trae_projects\scrape")
OUTPUT_DIR = BASE_DIR / "output"
SNAPSHOT_V1_F = OUTPUT_DIR / "sprachnudel_raw.snapshot.jsonl"
PATCH_F = OUTPUT_DIR / "sprachnudel_targeted_patch.jsonl"
OUT_F = OUTPUT_DIR / "sprachnudel_raw.snapshot.v2.jsonl"


def load_map(path: Path) -> dict[str, dict]:
    data = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            word = (entry.get("suchwort") or "").strip()
            if word:
                data[word.casefold()] = entry
    return data


def main():
    snapshot = load_map(SNAPSHOT_V1_F)
    patch = load_map(PATCH_F)

    merged = dict(snapshot)
    merged.update(patch)

    with open(OUT_F, "w", encoding="utf-8") as f:
        for key in sorted(merged, key=str.casefold):
            f.write(json.dumps(merged[key], ensure_ascii=False) + "\n")

    new_words = sum(1 for key in patch if key not in snapshot)
    overwritten = len(patch) - new_words

    print(f"Snapshot v2 geschrieben: {OUT_F}")
    print(f"Woerter vorher: {len(snapshot)}")
    print(f"Patch-Eintraege: {len(patch)}")
    print(f"Neue Woerter: {new_words}")
    print(f"Ersetzte/Reparierte Woerter: {overwritten}")
    print(f"Woerter nach Merge: {len(merged)}")


if __name__ == "__main__":
    main()
