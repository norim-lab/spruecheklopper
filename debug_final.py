import requests

url = "https://www.lyrikecke.de/reimlexikon"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": "https://www.lyrikecke.de",
    "Referer": "https://www.lyrikecke.de/reimlexikon",
}

# The only thing that changed is that we've been sending LOTS of requests.
# They might have banned our IP or set a cookie requirement.
# Let's test with a different proxy or without proxy.
# We'll use httpbin to see our request
import json

resp = requests.post("https://httpbin.org/post", data={"rhyme_term": "abendrot"}, headers=headers)
print("Was wir senden:")
print(json.dumps(resp.json(), indent=2))

# Was wenn wir statt rhyme_term einfach term senden?
data = {"term": "abendrot"}
resp2 = requests.post(url, data=data, headers=headers)
print(f"\nStatus: {resp2.status_code}, Length: {len(resp2.text)}")
if "bootstrapTable" in resp2.text:
    print("GEFUNDEN!")
else:
    print("NICHT gefunden")
