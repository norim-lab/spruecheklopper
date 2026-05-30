import requests

# Lyrikecke might have changed the form action entirely, or we need to use the autocomplete endpoint instead,
# or they enabled a Web Application Firewall (WAF) that detects python-requests.
# To test if it's a WAF (like Cloudflare), we can use curl_cffi or cloudscraper.

try:
    import cloudscraper
except ImportError:
    print("cloudscraper not installed, please install it: pip install cloudscraper")
    import sys
    sys.exit(1)

url = "https://www.lyrikecke.de/reimlexikon"
scraper = cloudscraper.create_scraper()  # returns a CloudScraper instance

data = {
    "rhyme_term": "abendrot",
    "rhyme_precision": "2",
    "rhyme_limit_term": "1"
}

resp = scraper.post(url, data=data)
print(f"Status Code (Cloudscraper): {resp.status_code}")
print(f"Länge der Antwort: {len(resp.text)}")

if "bootstrapTable" in resp.text:
    print("GEFUNDEN mit Cloudscraper!")
else:
    print("NICHT gefunden")
