import requests

url = "https://www.lyrikecke.de/reimlexikon"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": "https://www.lyrikecke.de",
    "Referer": "https://www.lyrikecke.de/reimlexikon",
}

# Versuch mit exakt den POST Parametern aus dem Browser
data = {
    "rhyme_term": "abendrot",
    "rhyme_precision": "2",
    "rhyme_limit_term": "1",
    "save": ""
}

print("Sende POST Request mit Chrome-Headers (inkl. save)...")
resp = requests.post(url, data=data, headers=headers)
print(f"Status Code: {resp.status_code}")
print(f"Länge der Antwort: {len(resp.text)}")

if "bootstrapTable" in resp.text:
    print("ERFOLG: bootstrapTable gefunden!")
else:
    print("FEHLER: bootstrapTable nicht gefunden.")
