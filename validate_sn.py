import json

entries = []
with open("output/sprachnudel_raw.jsonl", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        entries.append(json.loads(line))

print(f"=== {len(entries)} Entries ===")
print()
for e in entries:
    reime = [r for r in e["results"] if r["wortart"] != "suchwort"]
    ct = e["count"]
    pl = len(reime)
    ok = "OK" if ct == pl else f"MISMATCH (delta={pl-ct})"
    print(f"  {e['suchwort']:<20} | klang={e['klang']:<8} | count={ct:>3} | parsed={pl:>3} | {ok}")
    # Zeige kaputte Einträge (mehrwortig)
    bad = [r for r in reime if " " in r["wort"]]
    if bad:
        print(f"    *** MEHRWORTIGE EINTRÄGE: {[r['wort'] for r in bad]}")
