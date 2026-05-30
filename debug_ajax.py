import requests

url = "https://www.lyrikecke.de/reimlexikon/schnellsuche"
data = {
    "term": "abendrot"
}

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Encoding": "gzip",
    "X-Requested-With": "XMLHttpRequest"
}

print("Sende AJAX Request...")
resp = requests.get(url, params=data, headers=headers)

print(f"Status Code: {resp.status_code}")
print(f"Länge der Antwort: {len(resp.text)}")

if resp.status_code == 200:
    try:
        json_data = resp.json()
        print(f"ERFOLG: JSON geparst, Länge {len(json_data)}")
        if len(json_data) > 0:
            print(json_data[0])
    except Exception as e:
        print(f"Kein gültiges JSON: {e}")
        print(resp.text[:500])
else:
    print(resp.text[:500])
