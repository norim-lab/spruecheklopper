import requests

url = "https://www.lyrikecke.de/reimlexikon/result" # Ist es vielleicht result?
url_reim = "https://www.lyrikecke.de/reimlexikon/abendrot"

# Vielleicht ist es ja eine GET URL inzwischen?
print("Versuche GET auf /reimlexikon/abendrot ...")
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html",
    "Accept-Encoding": "gzip",
}
resp = requests.get(url_reim, headers=headers, allow_redirects=True)
print(f"Status: {resp.status_code}")
if "bootstrapTable" in resp.text:
    print("ERFOLG: bootstrapTable in GET URL gefunden!")
else:
    print("FEHLER in GET.")
    
# Lass uns prüfen was im Formular genau gesendet wird
# Wir machen es wie cURL
data_curl = "rhyme_term=abendrot&rhyme_precision=2&rhyme_limit_term=1"
resp_curl = requests.post("https://www.lyrikecke.de/reimlexikon", data=data_curl, headers={
    "User-Agent": "Mozilla/5.0",
    "Content-Type": "application/x-www-form-urlencoded"
})
print(f"Status curl POST: {resp_curl.status_code}")
if "bootstrapTable" in resp_curl.text:
    print("ERFOLG in curl POST!")
else:
    print("FEHLER in curl POST.")
