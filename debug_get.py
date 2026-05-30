import requests

# We need to GET first to grab the CSRF token, maybe?
url = "https://www.lyrikecke.de/reimlexikon"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip",
}

session = requests.Session()
session.headers.update(headers)

get_resp = session.get(url)
print(f"GET Status: {get_resp.status_code}")
print(f"GET Length: {len(get_resp.text)}")

# Let's save the GET output to see if there's a hidden token
with open("debug_get.html", "w", encoding="utf-8") as f:
    f.write(get_resp.text)
