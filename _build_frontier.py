import json
from pathlib import Path

OUTPUT = Path(__file__).parent / "output"
V6_FILE = OUTPUT / "sprachnudel_raw.snapshot.v6.merged.jsonl"
FRONTIER_FILE = OUTPUT / "sn_snowball_frontier.json"

print("=== Schneeball-Frontier aus v6 erstellen ===\n")

existing = set()
rhyme_words = set()

with open(V6_FILE, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        e = json.loads(line)
        sw = e.get("suchwort", "").casefold()
        if sw:
            existing.add(sw)
        if int(e.get("count", 0)) > 0:
            for r in e.get("results", []):
                rw = r.get("wort", "")
                if rw and len(rw) >= 2:
                    rhyme_words.add(rw)

print(f"v6 Eintraege: {len(existing)}")
print(f"Reimwoerter extrahiert: {len(rhyme_words)}")

new_words = [w for w in rhyme_words if w.casefold() not in existing]
new_words = list(dict.fromkeys(new_words))

print(f"Neu (nicht in v6): {len(new_words)}")

frontier = {
    "queue": new_words,
    "done": [],
    "saved_at": "",
}

with open(FRONTIER_FILE, "w", encoding="utf-8") as f:
    json.dump(frontier, f, ensure_ascii=False, indent=2)

print(f"\nFrontier gespeichert: {FRONTIER_FILE}")
print(f"Queue: {len(new_words)} Woerter")
