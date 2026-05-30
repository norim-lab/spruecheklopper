import requests

# Lyrikecke scheint Cloudflare oder ähnliches zu verwenden, was headless Requests als Bot erkennt
# und den POST request verwirft, aber keinen 403 gibt, sondern einfach die Seite neu lädt.
# Ein typisches Zeichen dafür ist, dass der POST Payload ignoriert wird.
# Lass uns tls-client oder cloudscraper testen.
# Zuerst schauen wir, ob wir requests.Session() richtig prepopulieren.

url = "https://www.lyrikecke.de/reimlexikon"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}

s = requests.Session()
# GET first
s.get(url, headers=headers)

# Now POST
post_headers = headers.copy()
post_headers["Content-Type"] = "application/x-www-form-urlencoded"
post_headers["Origin"] = "https://www.lyrikecke.de"
post_headers["Referer"] = "https://www.lyrikecke.de/reimlexikon"

data = {
    "rhyme_term": "abendrot",
    "rhyme_precision": "2",
    "rhyme_limit_term": "1",
    "save": ""
}

resp = s.post(url, data=data, headers=post_headers)
print("Session Versuch:", len(resp.text))
if "bootstrapTable" in resp.text:
    print("GEFUNDEN mit Session!")
else:
    print("NICHT gefunden")
    
# Lass uns einen GET request mit den GET Parametern testen.
# Funktioniert Lyrikecke vielleicht mit GET anstatt POST?
get_url = "https://www.lyrikecke.de/reimlexikon?rhyme_term=abendrot&rhyme_precision=2&rhyme_limit_term=1"
resp_get = s.get(get_url, headers=headers)
print("GET Versuch:", len(resp_get.text))
if "bootstrapTable" in resp_get.text:
    print("GEFUNDEN mit GET Payload!")
else:
    print("NICHT gefunden")
