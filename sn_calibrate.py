import argparse
import json
import time
from pathlib import Path

import requests

from sn_targeted_scrape import AdaptiveThrottle, HEADERS, RhymeParser, build_entry


BASE_DIR = Path(r"C:\Users\miron\Documents\trae_projects\scrape")
OUTPUT_DIR = BASE_DIR / "output"
BASE_URL = "https://www.sprachnudel.de/search"

DEFAULT_WORDS_F = OUTPUT_DIR / "sn_calibration_words.txt"
RESULTS_F = OUTPUT_DIR / "sn_calibration_results.json"
REPORT_F = OUTPUT_DIR / "sn_calibration_report.json"


def load_words(path: Path) -> list[str]:
    words = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            word = line.strip()
            if word and not word.startswith("#"):
                words.append(word)
    return words


def fetch_once(session: requests.Session, word: str):
    response = session.get(
        BASE_URL,
        params={"term": word, "type": "rhyme", "extended": "1"},
        timeout=20,
    )
    parser = RhymeParser()
    if response.status_code == 200:
        parser.feed(response.text)
        entry = build_entry(word, parser)
        return response.status_code, entry, parser
    return response.status_code, None, parser


def parse_args():
    parser = argparse.ArgumentParser(description="Kalibrierung fuer Sprachnudel-Parser")
    parser.add_argument("--words-file", default=str(DEFAULT_WORDS_F))
    parser.add_argument("--results-file", default=str(RESULTS_F))
    parser.add_argument("--report-file", default=str(REPORT_F))
    parser.add_argument("--base-delay", type=float, default=1.0)
    parser.add_argument("--min-delay", type=float, default=0.7)
    parser.add_argument("--max-delay", type=float, default=6.0)
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


def main():
    args = parse_args()
    words = load_words(Path(args.words_file))
    if args.limit > 0:
        words = words[: args.limit]

    throttle = AdaptiveThrottle(args.base_delay, args.min_delay, args.max_delay)
    session = requests.Session()
    session.headers.update(HEADERS)

    results = []
    next_request_ts = time.time()

    for idx, word in enumerate(words, start=1):
        now = time.time()
        if next_request_ts > now:
            time.sleep(next_request_ts - now)

        started = time.time()
        row = {
            "word": word,
            "status": None,
            "header_count": 0,
            "parsed_count": 0,
            "count_match": False,
            "duplicate_count": 0,
            "categories": [],
            "elapsed_ms": 0,
            "delay_after": None,
            "note": "",
        }

        try:
            status, entry, parser = fetch_once(session, word)
            row["status"] = status
            row["elapsed_ms"] = round((time.time() - started) * 1000, 1)

            if status == 200:
                parsed_results = [item for item in entry["results"] if item.get("wortart") != "suchwort"]
                unique_keys = {
                    (item.get("wortart"), item.get("silben"), (item.get("wort") or "").casefold())
                    for item in parsed_results
                }
                row["header_count"] = int(parser.count or 0)
                row["parsed_count"] = len(parsed_results)
                row["count_match"] = row["header_count"] == row["parsed_count"]
                row["duplicate_count"] = len(parsed_results) - len(unique_keys)
                row["categories"] = sorted({item.get("wortart") for item in parsed_results})
                row["note"] = "ok" if row["count_match"] else "header_count != parsed_count"
                speed_msg = throttle.on_success()
                if speed_msg:
                    row["note"] += f" | {speed_msg}"
            else:
                row["note"] = f"HTTP {status}"
                for _msg in throttle.on_error("http"):
                    row["note"] += f" | {_msg}"

        except requests.exceptions.RequestException as exc:
            row["status"] = "network"
            row["note"] = str(exc)
            for _msg in throttle.on_error("network"):
                row["note"] += f" | {_msg}"

        row["delay_after"] = throttle.delay
        results.append(row)
        next_request_ts = time.time() + throttle.delay

        if idx % 25 == 0 or idx == len(words):
            print(
                f"[{idx}/{len(words)}] "
                f"ok={sum(1 for r in results if r['status'] == 200)} "
                f"mismatch={sum(1 for r in results if r['status'] == 200 and not r['count_match'])} "
                f"errors={sum(1 for r in results if r['status'] != 200)} "
                f"delay={throttle.delay:.2f}s"
            )

    ok_rows = [r for r in results if r["status"] == 200]
    mismatches = [r for r in ok_rows if not r["count_match"]]
    duplicates = [r for r in ok_rows if r["duplicate_count"] > 0]
    report = {
        "total": len(results),
        "ok": len(ok_rows),
        "errors": len(results) - len(ok_rows),
        "count_matches": len(ok_rows) - len(mismatches),
        "count_mismatches": len(mismatches),
        "duplicate_cases": len(duplicates),
        "match_rate_pct": round(((len(ok_rows) - len(mismatches)) / len(ok_rows) * 100), 2) if ok_rows else 0.0,
        "avg_elapsed_ms": round(sum(r["elapsed_ms"] for r in ok_rows) / len(ok_rows), 1) if ok_rows else 0.0,
        "final_delay": throttle.delay,
        "bad_words": [r["word"] for r in mismatches[:50]],
        "error_words": [r["word"] for r in results if r["status"] != 200][:50],
    }

    with open(args.results_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    with open(args.report_file, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
