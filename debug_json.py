import requests

# Was wenn wir statt requests.post(url, data={...}) die Parameter im Body als JSON schicken müssen?
url = "https://www.lyrikecke.de/reimlexikon"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Content-Type": "application/json",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

print("Teste JSON POST...")
r1 = requests.post(url, json={"rhyme_term": "abendrot", "rhyme_precision": "2", "rhyme_limit_term": "1"}, headers=headers)
print(f"Status: {r1.status_code}")
print(f"Länge: {len(r1.text)}")

if "bootstrapTable" in r1.text:
    print("GEFUNDEN!")
else:
    print("NICHT gefunden")

# Vielleicht eine Blockade auf User-Agent-Ebene?
# Lyrikecke blockiert uns evtl. weil die Requests von einem Cloud-Provider oder unserer IP kommen.
# Ich prüfe ob es ein Captcha ist, indem ich nach "Cloudflare" oder "Access denied" suche
print(f"Erste 500 Zeichen:\n{r1.text[:500]}")
