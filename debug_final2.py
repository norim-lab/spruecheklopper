import requests

url = "https://www.lyrikecke.de/reimlexikon"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "de,en-US;q=0.7,en;q=0.3",
    "Accept-Encoding": "gzip",
    "Origin": "https://www.lyrikecke.de",
    "Referer": "https://www.lyrikecke.de/reimlexikon",
}

# The original HTML form inputs:
# <input type="text" id="rhyme_term" name="rhyme_term" value="abendrot" />
# <select id="rhyme_precision" name="rhyme_precision"><option value="2" selected></option></select>
# <input type="checkbox" id="rhyme_limit_term" name="rhyme_limit_term" value="1" checked />
# <button type="submit" class="btn btn-default" name="save">Reim suchen</button>

data = {
    "rhyme_term": "abendrot",
    "rhyme_precision": "2",
    "rhyme_limit_term": "1",
    "save": "Reim suchen" # Exactly as it is on the button! Sometimes backend checks the button value.
}

resp = requests.post(url, data=data, headers=headers)
print(f"Status Code: {resp.status_code}")
print(f"Länge der Antwort: {len(resp.text)}")

if "bootstrapTable" in resp.text:
    print("GEFUNDEN!")
else:
    print("NICHT gefunden")
