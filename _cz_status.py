import json, time

pf = 'output/sprachnudel_countzero_patch.jsonl'
entries = []
with open(pf, 'r', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line:
            entries.append(json.loads(line))

total = len(entries)
blocked_true = sum(1 for e in entries if e.get('blocked') is True)
blocked_false = sum(1 for e in entries if e.get('blocked') is False)
with_rhymes = sum(1 for e in entries if int(e.get('count', 0)) > 0)
still_zero = sum(1 for e in entries if int(e.get('count', 0)) == 0)

d = json.load(open('output/sn_countzero_progress.json', encoding='utf-8'))
c = json.load(open('output/sn_countzero_control.json', encoding='utf-8'))

elapsed = d.get('started_at', 0)
run_s = int(time.time() - elapsed) if elapsed else 0
rem = d['total'] - d['completed']
base = d.get('run_completed_base', 0)
rate = (d['completed'] - base) / run_s if run_s > 0 else 0
eta = int(rem / rate) if rate > 0 else 0

print(f"=== Count=0 Rescrape Status ===")
print(f"Control: {c['status']} - {c.get('msg', '')}")
print(f"")
print(f"Patch-Eintraege: {total}")
print(f"  blocked=true:  {blocked_true} (Wörter die GEBLOCKT wurden)")
print(f"  blocked=false: {blocked_false} (erfolgreich gescrapt)")
print(f"  Mit Reimen:    {with_rhymes}")
print(f"  Immer noch 0:  {still_zero}")
print(f"")
print(f"Fortschritt: {d['completed']}/{d['total']} ({d['completed']*100/d['total']:.1f}%)")
print(f"Gefunden: {d['found']}")
print(f"Leer: {d['still_empty']}")
print(f"Fehler: {d['errors']}")
print(f"Blocked: {d['blocked']}")
print(f"Rate: {rate:.2f}/s")
print(f"Laufzeit: {run_s//3600}h {(run_s%3600)//60}min")
print(f"ETA: {eta//3600}h {(eta%3600)//60}min")
print(f"Cookie-Refreshs: {d.get('cookie_refreshes', 'n/a (browser-mode)')}")
print(f"Updated: {d['updated_at']}")
print(f"")
print(f"Verbleibend zum Scrapen: {rem} Wörter")
print(f"Diese {rem} Wörter (inkl. evtl. geblockter) werden automatisch beim naechsten Start versucht.")
