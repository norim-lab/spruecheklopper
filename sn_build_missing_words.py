import json
from pathlib import Path

from sprachnudel_scraper import load_sitemap


BASE_DIR = Path(r"C:\Users\miron\Documents\trae_projects\scrape")
OUTPUT_DIR = BASE_DIR / "output"
SNAPSHOT_F = OUTPUT_DIR / "sprachnudel_raw.snapshot.jsonl"
CAL_RESULTS_F = OUTPUT_DIR / "sn_calibration_results.json"
OUT_F = OUTPUT_DIR / "sn_missing_words.txt"

PRIORITY_WORDS = [
    "magd",
    "haus",
    "raum",
    "test",
    "baum",
    "liebe",
    "herz",
    "zeit",
    "nacht",
    "licht",
    "feuer",
    "leben",
    "hand",
    "wind",
]


def load_snapshot_index() -> dict[str, dict]:
    index = {}
    with open(SNAPSHOT_F, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            word = (entry.get("suchwort") or "").strip()
            if word:
                index[word.casefold()] = entry
    return index


def load_positive_calibration_gaps(snapshot_index: dict[str, dict]) -> list[str]:
    if not CAL_RESULTS_F.exists():
        return []
    data = json.load(open(CAL_RESULTS_F, "r", encoding="utf-8"))
    missing_out = []
    repair_out = []
    for row in data:
        word = (row.get("word") or "").strip()
        if not word:
            continue
        if row.get("status") != 200 or int(row.get("parsed_count", 0) or 0) <= 0:
            continue
        snapshot_entry = snapshot_index.get(word.casefold())
        if snapshot_entry is None:
            missing_out.append(word)
            continue
        snapshot_count = int(snapshot_entry.get("count", 0) or 0)
        snapshot_results = len(snapshot_entry.get("results", []))
        if snapshot_count <= 0 or snapshot_results <= 1:
            repair_out.append(word)
    return missing_out, repair_out


def dedupe(words: list[str]) -> list[str]:
    seen = set()
    out = []
    for word in words:
        key = word.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(word)
    return out


def main():
    snapshot_index = load_snapshot_index()
    snapshot_words = set(snapshot_index.keys())
    sitemap_words = load_sitemap()

    missing_from_sitemap = [w for w in sitemap_words if w.casefold() not in snapshot_words]
    positive_calibration_gaps, positive_calibration_repairs = load_positive_calibration_gaps(snapshot_index)
    priority_repairs = []
    priority_missing = []
    for w in PRIORITY_WORDS:
        entry = snapshot_index.get(w.casefold())
        if entry is None:
            priority_missing.append(w)
            continue
        if int(entry.get("count", 0) or 0) <= 0 or len(entry.get("results", [])) <= 1:
            priority_repairs.append(w)

    words = dedupe(priority_missing + priority_repairs + positive_calibration_repairs + positive_calibration_gaps + missing_from_sitemap)

    OUT_F.parent.mkdir(exist_ok=True)
    with open(OUT_F, "w", encoding="utf-8") as f:
        for word in words:
            f.write(word + "\n")

    print(f"Fehlwortliste geschrieben: {OUT_F}")
    print(f"Prioritaet fehlend: {len(priority_missing)}")
    print(f"Prioritaet Reparatur: {len(priority_repairs)}")
    print(f"Kalibrierungs-Gaps mit Treffern: {len(positive_calibration_gaps)}")
    print(f"Kalibrierungs-Reparaturen mit Treffern: {len(positive_calibration_repairs)}")
    print(f"Sitemap-Gaps: {len(missing_from_sitemap)}")
    print(f"Gesamt unique: {len(words)}")
    print("Beispiel:", words[:25])


if __name__ == "__main__":
    main()
