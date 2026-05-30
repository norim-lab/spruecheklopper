import requests
from bs4 import BeautifulSoup

url = "https://www.lyrikecke.de/reimlexikon"
data = {
    "rhyme_term": "abendrot",
    "rhyme_precision": "2",
    "rhyme_limit_term": "1",
}

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip",  # Brotli weglassen für sauberen Text
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": "https://www.lyrikecke.de",
    "Referer": "https://www.lyrikecke.de/reimlexikon",
}

resp = requests.post(url, data=data, headers=headers)
print(f"Status Code: {resp.status_code}")
print(f"Länge der Antwort: {len(resp.text)}")

soup = BeautifulSoup(resp.text, 'html.parser')
title = soup.find('title')
print(f"Seitentitel: {title.text if title else 'Kein Titel'}")

# Lass uns nach Fehlermeldungen oder Captchas suchen
for p in soup.find_all(['p', 'h1', 'h2', 'div']):
    if "Fehler" in p.text or "Error" in p.text or "Captcha" in p.text or "blockiert" in p.text:
        print(f"Verdächtiger Text gefunden: {p.text.strip()[:100]}")

# Gibt es ein Formular?
form = soup.find('form')
if form:
    print(f"Formular gefunden: action={form.get('action')}, method={form.get('method')}")
else:
    print("Kein Formular gefunden.")

if "bootstrapTable" in resp.text:
    print("ERFOLG: bootstrapTable gefunden!")
else:
    print("FEHLER: bootstrapTable nicht gefunden.")
    print("\nHier sind die ersten 1000 Zeichen der Antwort:")
    print(resp.text[:1000])
