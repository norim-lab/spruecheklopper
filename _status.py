import json, time

d = json.load(open('output/sn_sitemap_progress.json', encoding='utf-8'))
c = json.load(open('output/sn_sitemap_control.json', encoding='utf-8'))

elapsed = d.get('started_at', 0)
run_s = int(time.time() - elapsed) if elapsed else 0
rem = d['total'] - d['completed']
base = d.get('run_completed_base', 0)
rate = (d['completed'] - base) / run_s if run_s > 0 else 0
eta = int(rem / rate) if rate > 0 else 0

print(f"Status: {c['status']}")
print(f"Fortschritt: {d['completed']}/{d['total']} ({d['completed']*100/d['total']:.1f}%)")
print(f"Mit Reimen: {d['found']}")
print(f"Leer: {d['still_empty']}")
print(f"Fehler: {d['errors']}")
print(f"Blocked: {d['blocked']}")
print(f"Rate: {rate:.2f}/s")
print(f"Laufzeit: {run_s//3600}h {(run_s%3600)//60}min")
print(f"ETA: {eta//3600}h {(eta%3600)//60}min")
print(f"Cookie-Refreshs: {d['cookie_refreshes']}")
print(f"Updated: {d['updated_at']}")
