import requests
import re

# Okay, it's not cloudflare blocking us, because we get a 200 OK and it returns the default form page.
# This means the server is explicitly deciding not to process our POST data.
# This happens in PHP when:
# 1. CSRF token is missing
# 2. Content-Type is wrong
# 3. Form field names don't match EXACTLY
# 4. There's a hidden cookie or session requirement we're not fulfilling

url = "https://www.lyrikecke.de/reimlexikon"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "de,en-US;q=0.7,en;q=0.3",
    "Accept-Encoding": "gzip",
    "Connection": "keep-alive",
}

s = requests.Session()
# First request to get cookies
r1 = s.get(url, headers=headers)
print("Cookies received:", s.cookies.get_dict())

# Parse ALL inputs from the form to make sure we miss NOTHING
from bs4 import BeautifulSoup
soup = BeautifulSoup(r1.text, 'html.parser')
form = soup.find('form', action='/reimlexikon')

data = {}
if form:
    for inp in form.find_all(['input', 'select', 'button']):
        name = inp.get('name')
        if not name:
            continue
            
        # Checkbox logic
        if inp.name == 'input' and inp.get('type') == 'checkbox':
            if inp.has_attr('checked'):
                data[name] = inp.get('value', 'on')
        # Select logic
        elif inp.name == 'select':
            selected = inp.find('option', selected=True)
            if selected:
                data[name] = selected.get('value')
            else:
                data[name] = inp.find('option').get('value')
        # Standard input logic
        else:
            val = inp.get('value', '')
            data[name] = val
            
print("\nExtracted Form Data:", data)

# Modify for our specific search
data['rhyme_term'] = 'abendrot'
data['rhyme_precision'] = '2'

print("\nSending Form Data:", data)

headers["Content-Type"] = "application/x-www-form-urlencoded"
headers["Origin"] = "https://www.lyrikecke.de"
headers["Referer"] = "https://www.lyrikecke.de/reimlexikon"

r2 = s.post(url, data=data, headers=headers)
print(f"Status Code: {r2.status_code}")
print(f"Length: {len(r2.text)}")

if "bootstrapTable" in r2.text:
    print("GEFUNDEN!")
else:
    print("NICHT gefunden")
