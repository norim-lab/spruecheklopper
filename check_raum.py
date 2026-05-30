import json
progress_f = "output/sn_fix_progress.json"
done = set(json.load(open(progress_f, encoding="utf-8")))
print(f"Gesamt erledigt: {len(done)}")
print(f"'Raum' erledigt: {'Raum' in done}")
if "Raum" in done:
    with open("output/sprachnudel_raw.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            e = json.loads(line)
            if e["suchwort"] == "Raum":
                print(f"Raum: count={e['count']}, results={len(e['results'])}")
                if e["count"] > 0:
                    print([r["wort"] for r in e["results"][:10]])
                break
