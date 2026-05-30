"""
sn_find_gaps.py
Vergleicht die aktuelle sprachnudel.de Sitemap mit dem snapshot.v2
und identifiziert alle fehlenden Wörter.
"""
import gzip
import json
import re
import sys
from pathlib import Path

import requests

BASE_DIR = Path(r"C:\Users\miron\Documents\trae_projects\scrape")
OUTPUT_DIR = BASE_DIR / "output"
SITEMAP_URL = "https://www.sprachnudel.de/sitemap-word-0.xml.gz"
SNAPSHOT_V2 = OUTPUT_DIR / "sprachnudel_raw.snapshot.v2.jsonl"
GAP_FILE = OUTPUT_DIR / "sn_gap_words.txt"
GAP_REPORT = OUTPUT_DIR / "sn_gap_report.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "de-DE,de;q=0.9",
}


def load_sitemap_words() -> list[str]:
    print("Lade Sitemap von sprachnudel.de ...")
    r = requests.get(SITEMAP_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    raw_xml = gzip.decompress(r.content).decode("utf-8")

    urls = re.findall(
        r'<loc>https://www\.sprachnudel\.de/woerterbuch/(.*?)</loc>',
        raw_xml
    )

    from urllib.parse import unquote
    words = []
    seen = set()
    for url_part in urls:
        decoded = unquote(url_part).strip()
        if "-" in decoded:
            continue
        decoded = re.sub(r'_\d+$', '', decoded)
        key = decoded.casefold()
        if key not in seen:
            seen.add(key)
            words.append(decoded)
    print(f"Sitemap: {len(words)} eindeutige Wörter geladen")
    return words


def load_snapshot_words() -> set[str]:
    print(f"Lade Snapshot: {SNAPSHOT_V2.name} ...")
    words = set()
    with open(SNAPSHOT_V2, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                w = entry.get("suchwort", "")
                if w:
                    words.add(w.casefold())
            except json.JSONDecodeError:
                continue
    print(f"Snapshot: {len(words)} Wörter geladen")
    return words


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    sitemap_words = load_sitemap_words()
    snapshot_words = load_snapshot_words()

    missing = []
    for word in sitemap_words:
        if word.casefold() not in snapshot_words:
            missing.append(word)

    missing_sorted = sorted(missing, key=str.casefold)

    print(f"\n=== ERGEBNIS ===")
    print(f"Sitemap-Wörter:  {len(sitemap_words)}")
    print(f"Snapshot-Wörter: {len(snapshot_words)}")
    print(f"Fehlend:         {len(missing_sorted)}")

    if missing_sorted:
        print(f"\nErste 50 fehlende Wörter:")
        for w in missing_sorted[:50]:
            print(f"  - {w}")
        if len(missing_sorted) > 50:
            print(f"  ... und {len(missing_sorted) - 50} weitere")

    with open(GAP_FILE, "w", encoding="utf-8") as f:
        for w in missing_sorted:
            f.write(w + "\n")
    print(f"\nFehlende Wörter gespeichert in: {GAP_FILE}")

    report = {
        "sitemap_count": len(sitemap_words),
        "snapshot_count": len(snapshot_words),
        "missing_count": len(missing_sorted),
        "missing_sample": missing_sorted[:100],
    }
    with open(GAP_REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"Report gespeichert in: {GAP_REPORT}")


if __name__ == "__main__":
    main()
