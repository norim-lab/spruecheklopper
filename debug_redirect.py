import requests

# Eine letzte Möglichkeit, warum das POST fehlschlägt, aber das GET die richtige Seite liefert:
# Die Seite hat eine Subdomain oder Redirect Regel, z.B. /reimlexikon/ zu /reimlexikon (oder andersrum).
# Beim POST führt ein 301 Redirect dazu, dass der POST Payload verloren geht und als GET am Ziel ankommt!
# Das ist ein klassisches Problem.

url1 = "https://www.lyrikecke.de/reimlexikon"
url2 = "https://www.lyrikecke.de/reimlexikon/"
url3 = "http://www.lyrikecke.de/reimlexikon"
url4 = "https://lyrikecke.de/reimlexikon"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Content-Type": "application/x-www-form-urlencoded"
}

data = {
    "rhyme_term": "abendrot",
    "rhyme_precision": "2",
    "rhyme_limit_term": "1"
}

for u in [url1, url2, url3, url4]:
    print(f"\nTeste URL: {u}")
    resp = requests.post(u, data=data, headers=headers, allow_redirects=False)
    print(f"Status: {resp.status_code}")
    if resp.status_code in [301, 302]:
        print(f"Redirect nach: {resp.headers.get('Location')}")
    else:
        print(f"Length: {len(resp.text)}")
        if "bootstrapTable" in resp.text:
            print("GEFUNDEN!")
        else:
            print("Nicht gefunden.")
