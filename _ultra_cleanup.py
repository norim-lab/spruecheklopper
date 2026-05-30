import json
import re
import asyncio
import aiohttp

INPUT = "output/reimgruppen.json"
OUTPUT = "output/reimgruppen_final_v2.json"

BANNED_SUBSTRINGS = ["recht", "not", "lot", "ung", "ion", "ot", "on"]

def word_has_banned(wort):
    low = wort.lower()
    for b in BANNED_SUBSTRINGS:
        if b in low:
            return True
    return False

with open(INPUT, "r", encoding="utf-8") as f:
    gruppen = json.load(f)

print(f"Input: {len(gruppen)} Gruppen, {sum(len(g.get('woerter', [])) for g in gruppen)} Woerter")

stat_pointe = 0
stat_banned = 0
stat_score_filtered = 0
stat_dup = 0
stat_groups_removed = 0

clean = []

for g in gruppen:
    woerter = g.get("woerter", [])

    for w in woerter:
        if "pointe" in w:
            if "pointe_score" not in w:
                w["pointe_score"] = 5 if w["pointe"] else 0
            del w["pointe"]
            stat_pointe += 1
        if "pointe_score" not in w:
            w["pointe_score"] = 0

    seen = set()
    deduped = []
    for w in woerter:
        key = w.get("wort", "").lower()
        if key not in seen:
            seen.add(key)
            deduped.append(w)
        else:
            stat_dup += 1
    woerter = deduped

    kept = []
    for w in woerter:
        wort = w.get("wort", "")
        if word_has_banned(wort):
            stat_banned += 1
            continue
        if not w.get("laendlich") and w.get("pointe_score", 0) <= 1:
            stat_score_filtered += 1
            continue
        kept.append(w)
    woerter = kept

    good = sum(1 for w in woerter if w.get("laendlich") and w.get("pointe_score", 0) >= 3)
    if good < 2:
        stat_groups_removed += 1
        continue

    g["woerter"] = woerter
    g["kontext"] = []
    g["derb_potenzial"] = any(
        w.get("laendlich") and w.get("pointe_score", 0) >= 3 for w in woerter
    )
    clean.append(g)

tw = sum(len(g.get("woerter", [])) for g in clean)
print()
print("--- Ultra Strict Cleanup ---")
print(f"pointe-Felder entfernt:   {stat_pointe}")
print(f"Woerter banned-Substring: {stat_banned}")
print(f"Woerter score+laendlich:  {stat_score_filtered}")
print(f"Wort-Duplikate:           {stat_dup}")
print(f"Gruppen entfernt (<2 gut):{stat_groups_removed}")
print()
print(f"Nach Cleanup: {len(clean)} Gruppen, {tw} Woerter")

with open("output/_pre_kontext.json", "w", encoding="utf-8") as f:
    json.dump(clean, f, ensure_ascii=False, indent=2)
print("-> output/_pre_kontext.json (Kontext noch leer)")
