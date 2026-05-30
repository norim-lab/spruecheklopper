import requests

url = "https://www.lyrikecke.de/reimlexikon"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip",
}

# Test with GET to /reimlexikon/{word} - this is how many sites handle it after POSTing
# Wait, look at action="/reimlexikon" method="post"

# Let's inspect the HTML of the POST response. If it's 14612 bytes, it's the exact same size as the GET response (14612).
# Das heißt, er ignoriert den POST komplett und liefert einfach die Startseite aus!
# Warum ignoriert er den POST?
# Vielleicht liegt es an der URL? Lyrikecke erzwingt vielleicht www. oder https://? (Wir haben beides).

# Lass uns testen ob requests automatisch weiterleitet (allow_redirects=False)
resp = requests.post(url, data={"rhyme_term": "abendrot", "rhyme_precision": "2"}, headers=headers, allow_redirects=False)
print(f"Status Code: {resp.status_code}")
if resp.status_code in [301, 302, 303, 307, 308]:
    print(f"Redirect to: {resp.headers.get('Location')}")
    
# Maybe trailing slash?
url2 = "https://www.lyrikecke.de/reimlexikon/"
resp2 = requests.post(url2, data={"rhyme_term": "abendrot", "rhyme_precision": "2"}, headers=headers, allow_redirects=False)
print(f"Status Code mit trailing slash: {resp2.status_code}")
if resp2.status_code in [301, 302, 303, 307, 308]:
    print(f"Redirect to: {resp2.headers.get('Location')}")
