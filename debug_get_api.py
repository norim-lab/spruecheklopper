import requests

url = "https://www.lyrikecke.de/reimlexikon/schnellsuche"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Accept": "*/*",
    "X-Requested-With": "XMLHttpRequest",
    "Connection": "keep-alive"
}

params = {
    "term": "abendrot"
}

resp = requests.get(url, params=params, headers=headers)
print(f"Schnellsuche Status: {resp.status_code}")
if resp.status_code == 200:
    print(resp.text[:500])
else:
    print("Nicht gefunden")
    
# Lass uns nochmal die normale Suche per GET probieren
url2 = "https://www.lyrikecke.de/reimlexikon/suche"
params2 = {
    "rhyme_term": "abendrot",
    "rhyme_precision": "2"
}
resp2 = requests.get(url2, params=params2, headers=headers)
print(f"Suche GET Status: {resp2.status_code}")

# Was ist wenn das Formular über einen anderen Pfad gesendet wird?
# Wir hatten im HTML: action="/reimlexikon"
