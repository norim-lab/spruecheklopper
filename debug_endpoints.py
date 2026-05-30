import requests
import json

url = "https://www.lyrikecke.de/reimlexikon"
# Es gibt vielleicht einen API-Endpunkt, den wir nicht gesehen haben.
# Wir können `reimlexikon.json` versuchen.
url_json = "https://www.lyrikecke.de/reimlexikon.json"
url_search = "https://www.lyrikecke.de/reimlexikon/search"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

print("Teste /reimlexikon.json")
r1 = requests.post(url_json, data={"rhyme_term": "abendrot"}, headers=headers)
print(f"Status: {r1.status_code}, Length: {len(r1.text)}")

print("\nTeste /reimlexikon/search")
r2 = requests.post(url_search, data={"rhyme_term": "abendrot"}, headers=headers)
print(f"Status: {r2.status_code}, Length: {len(r2.text)}")

print("\nTeste GET /reimlexikon/abendrot")
r3 = requests.get("https://www.lyrikecke.de/reimlexikon/abendrot", headers=headers)
print(f"Status: {r3.status_code}, Length: {len(r3.text)}")
