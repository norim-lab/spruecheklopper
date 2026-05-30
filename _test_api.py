import json, urllib.request
r = urllib.request.urlopen('http://127.0.0.1:5001/api/scrape-status')
d = json.loads(r.read())
rs = d['rescrape']
print(f"mode={rs['mode']} completed={rs['completed']} total={rs['total']} fixed={rs['fixed']} rate={rs['rate']}")
print(f"control: {d['control']['status']} — {d['control']['msg']}")
