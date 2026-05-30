import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

# Okay, they definitely block programmatic POST requests to /reimlexikon.
# Let's try simulating the EXACT request flow of a browser using requests.Session()
# and parsing all the hidden fields + proper referrer + headers.

url = "https://www.lyrikecke.de/reimlexikon"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "de,en-US;q=0.7,en;q=0.3",
    "Accept-Encoding": "gzip",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

session = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[502, 503, 504])
session.mount('https://', HTTPAdapter(max_retries=retries))

# Step 1: Get the page
resp1 = session.get(url, headers=headers)
print(f"GET Status: {resp1.status_code}")

soup = BeautifulSoup(resp1.text, 'html.parser')
form = soup.find('form', action='/reimlexikon')

data = {}
for input_tag in form.find_all('input'):
    name = input_tag.get('name')
    if name:
        if input_tag.get('type') == 'checkbox':
            if input_tag.has_attr('checked'):
                data[name] = input_tag.get('value', '1')
        else:
            data[name] = input_tag.get('value', '')

for select_tag in form.find_all('select'):
    name = select_tag.get('name')
    if name:
        # Just use the default selected
        data[name] = '2'  # Or whatever precision we want

# Overwrite with our values
data['rhyme_term'] = 'abendrot'
data['rhyme_precision'] = '2'
data['rhyme_limit_term'] = '1'

# Important: button name/value
data['save'] = ''

# Also include the hidden token if any was found (data currently holds all inputs)
print("POST Data:", data)

headers["Content-Type"] = "application/x-www-form-urlencoded"
headers["Origin"] = "https://www.lyrikecke.de"
headers["Referer"] = "https://www.lyrikecke.de/reimlexikon"

resp2 = session.post(url, data=data, headers=headers)
print(f"POST Status: {resp2.status_code}, Length: {len(resp2.text)}")

if "bootstrapTable" in resp2.text:
    print("GEFUNDEN!")
else:
    print("FEHLER")
    # Let's see if we get the exact same HTML as the GET request
    if len(resp2.text) == len(resp1.text):
        print("Der Server hat den POST ignoriert und einfach die Startseite (GET) zurückgegeben!")
