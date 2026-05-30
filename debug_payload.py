import requests
import json

url = "https://www.lyrikecke.de/reimlexikon"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Content-Type": "application/x-www-form-urlencoded",
}

# The actual form field name from the Lyrikecke website:
# <input type="text" id="rhyme_term" name="rhyme[term]" ...
# Let's test with the proper array syntax or alternative names
payloads = [
    {"rhyme[term]": "abendrot", "rhyme[precision]": "2", "rhyme[limit_term]": "1"},
    {"term": "abendrot", "precision": "2"},
    {"rhyme_term": "abendrot", "rhyme_precision": "2", "save": ""}
]

for idx, data in enumerate(payloads):
    print(f"\n--- Test {idx+1} mit Payload: {data} ---")
    resp = requests.post(url, data=data, headers=headers)
    print(f"Status: {resp.status_code}, Length: {len(resp.text)}")
    if "bootstrapTable" in resp.text:
        print(">>> ERFOLG: bootstrapTable gefunden!")
    else:
        print("FEHLER: Nicht gefunden")
