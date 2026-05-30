from pathlib import Path
import json

patch = Path(r"c:\Users\miron\Documents\trae_projects\scrape\output\sprachnudel_rescrape_patch.jsonl")
prog = Path(r"c:\Users\miron\Documents\trae_projects\scrape\output\sn_rescrape_progress.json")

if patch.exists():
    lines = open(patch, "r", encoding="utf-8").readlines()
    print(f"Patch: {len(lines)} Eintraege, {patch.stat().st_size} bytes")
    for l in lines[:5]:
        e = json.loads(l.strip())
        sw = e.get("suchwort", "?")
        cnt = e.get("count", 0)
        print(f"  {sw}: count={cnt}")
else:
    print("Patch noch nicht erstellt")

if prog.exists():
    p = json.load(open(prog, "r", encoding="utf-8"))
    print(f"Completed: {len(p.get('completed', []))}")
else:
    print("Progress noch nicht erstellt")
