import json
from pathlib import Path

pf = Path(r"c:\Users\miron\Documents\trae_projects\scrape\output\sn_complete_progress.json")
patch_f = Path(r"c:\Users\miron\Documents\trae_projects\scrape\output\sprachnudel_complete_patch.jsonl")

with open(pf, "r", encoding="utf-8") as f:
    p = json.load(f)

completed = p.get("completed", [])
deferred = p.get("deferred", {})
stats = p.get("stats", {})
jobs = p.get("jobs", [])

print(f"=== COMPLETE SCRAPE STATUS ===")
print(f"Completed: {len(completed)}")
print(f"Deferred: {len(deferred)}")
print(f"Open jobs: {len(jobs)}")
print(f"Requests: {stats.get('requests',0)}")
print(f"Mit Reimen: {stats.get('success',0)}")
print(f"Leer: {stats.get('empty',0)}")
print()

test = ["Schwein", "Schaf", "Ziege", "Gans", "Pferd", "Mist", "Hund", "Kuh", "Katze",
        "Maus", "Wolf", "Frosch", "Fuchs", "Esel", "Hase", "Affe", "Bär", "Löwe",
        "Vogel", "Fisch", "Wurm", "Fliege", "Biene", "Ameise"]

completed_cf = set(c.casefold() for c in completed)
deferred_cf = set(d.casefold() for d in deferred)

print("=== TESTWOERTER ===")
for t in test:
    cf = t.casefold()
    if cf in completed_cf:
        print(f"  {t}: DONE")
    elif cf in deferred_cf:
        print(f"  {t}: DEFERRED")
    else:
        in_jobs = any(j.get("word","").casefold() == cf for j in jobs[:1000])
        print(f"  {t}: OFFEN (in jobs: {in_jobs})")

print()
print("=== DEFERRED SAMPLE ===")
for i, (k, v) in enumerate(list(deferred.items())[:10]):
    print(f"  {k}: {v.get('last_error','')} (attempts={v.get('attempts',0)})")

print()
p0_done = sum(1 for c in completed if any(c.casefold() == t.casefold() for t in ["aal","abend","acker"]))
print(f"P0 Thema-Wörter in completed: {p0_done}")
