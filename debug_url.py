import requests
import re

url = "https://www.lyrikecke.de/reimlexikon/abendrot"

# Ah, wenn wir einfach /reimlexikon/abendrot machen, 
# aber mit POST anstelle von GET? Nein, GET /reimlexikon/abendrot war ein 404.
# ABER in der lyrikecke2.js gibt es `ajax` Calls.
# Lass uns nochmal die Hauptseite durchsuchen nach JS Variablen, die die echte URL enthalten könnten.

resp = requests.get("https://www.lyrikecke.de/reimlexikon", headers={"User-Agent": "Mozilla/5.0"})
html = resp.text

print("Suche nach JSON oder URLs im HTML:")
matches = re.findall(r'/[a-zA-Z0-9_/-]+\.json', html)
for m in matches:
    print(m)

# Suche nach action
actions = re.findall(r'action="([^"]+)"', html)
print("Actions:", set(actions))

# What if it's NOT a 404 for POST /reimlexikon/abendrot?
print("Testing POST /reimlexikon/abendrot")
resp_post = requests.post(url, data={"rhyme_precision": "2"}, headers={"User-Agent": "Mozilla/5.0"})
print(f"Status: {resp_post.status_code}, Length: {len(resp_post.text)}")
if "bootstrapTable" in resp_post.text:
    print("GEFUNDEN!")
