import json
import random
from pathlib import Path


BASE_DIR = Path(r"C:\Users\miron\Documents\trae_projects\scrape")
OUTPUT_DIR = BASE_DIR / "output"
SNAPSHOT_F = OUTPUT_DIR / "sprachnudel_raw.snapshot.jsonl"
OUT_F = OUTPUT_DIR / "sn_calibration_words.txt"


CUSTOM_SHORT = [
    "magd", "haus", "raum", "baum", "test", "wind", "hand", "zeit", "herz", "nacht",
    "licht", "feuer", "leben", "liebe", "traum", "maus", "laus", "graus", "aus", "klaus",
]

CUSTOM_UMLAUT = [
    "straße", "grüße", "müde", "bär", "löwe", "küste", "größe", "schön", "früh", "süß",
    "blöße", "träume", "häuser", "fuß", "mäßig", "fröhlich", "köln", "ärger", "übermut", "größe",
]

CUSTOM_COMPOUND = [
    "krankenhaus", "treibhaus", "wirtshaus", "hochhaus", "baumhaus", "mutterhaus", "rathaus",
    "freiheraus", "geradeheraus", "familienhaus", "schauspielhaus", "treppenhaus", "glashaus",
    "lebkuchenhaus", "holzhaus", "wohnhaus", "raumfahrt", "hausverstand", "weinhaus", "blockhaus",
]

CUSTOM_MISC = [
    "jagd", "betagt", "gefragt", "klaus", "maus", "applaus", "reißaus", "aus", "kraus", "schmaus",
    "chaussiert", "hausen", "hausierer", "blockhaus", "weinhaus", "plaus", "draus", "hinaus", "heraus", "voraus",
]


def load_snapshot_rows():
    rows = []
    with open(SNAPSHOT_F, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            word = entry.get("suchwort", "").strip()
            if not word:
                continue
            count = int(entry.get("count", 0) or 0)
            results = entry.get("results", [])
            rows.append(
                {
                    "word": word,
                    "count": count,
                    "results_len": len(results),
                    "positive": count > 0 and len(results) > 1,
                    "length": len(word),
                    "short": len(word) <= 4,
                    "long": len(word) >= 12,
                    "alpha": word.isalpha(),
                }
            )
    return rows


def sample_bucket(rows, predicate, n, seed):
    candidates = [row["word"] for row in rows if predicate(row)]
    rng = random.Random(seed)
    rng.shuffle(candidates)
    return candidates[:n]


def dedupe_keep_order(words):
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
    rows = load_snapshot_rows()

    buckets = []
    buckets += sample_bucket(rows, lambda r: r["positive"] and r["count"] >= 50 and r["alpha"], 30, 101)
    buckets += sample_bucket(rows, lambda r: r["positive"] and 10 <= r["count"] < 50 and r["alpha"], 30, 102)
    buckets += sample_bucket(rows, lambda r: r["positive"] and 1 <= r["count"] < 10 and r["alpha"], 30, 103)
    buckets += sample_bucket(rows, lambda r: (not r["positive"]) and r["short"] and r["alpha"], 20, 104)
    buckets += sample_bucket(rows, lambda r: (not r["positive"]) and r["long"] and r["alpha"], 20, 105)
    buckets += sample_bucket(rows, lambda r: (not r["positive"]) and r["alpha"], 20, 106)
    buckets += CUSTOM_SHORT
    buckets += CUSTOM_UMLAUT
    buckets += CUSTOM_COMPOUND
    buckets += CUSTOM_MISC

    words = dedupe_keep_order(buckets)
    words = words[:200]

    OUT_F.parent.mkdir(exist_ok=True)
    with open(OUT_F, "w", encoding="utf-8") as f:
        for word in words:
            f.write(word + "\n")

    print(f"Kalibrierungsset geschrieben: {OUT_F}")
    print(f"Anzahl Woerter: {len(words)}")


if __name__ == "__main__":
    main()
