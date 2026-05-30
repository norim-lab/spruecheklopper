import requests

url = "https://www.lyrikecke.de/reimlexikon"
data = {
    "rhyme_term": "abendrot",
    "rhyme_precision": "2",
    "rhyme_limit_term": "1",
    "save": ""
}

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "de,en-US;q=0.7,en;q=0.3",
    "Accept-Encoding": "gzip, deflate, br",
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": "https://www.lyrikecke.de",
    "DNT": "1",
    "Connection": "keep-alive",
    "Referer": "https://www.lyrikecke.de/reimlexikon",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1"
}

print("Sende Request mit exakten Firefox Headern...")
session = requests.Session()
# Wichtig: Zuerst GET um Session Cookie (PHPSESSID) zu bekommen, falls es das braucht
session.get(url, headers=headers)
print("Cookies nach GET:", session.cookies.get_dict())

resp = session.post(url, data=data, headers=headers)
print(f"Status Code: {resp.status_code}")
print(f"Länge der Antwort: {len(resp.text)}")

if "bootstrapTable" in resp.text:
    print("ERFOLG: bootstrapTable gefunden!")
else:
    print("FEHLER: bootstrapTable nicht gefunden.")
