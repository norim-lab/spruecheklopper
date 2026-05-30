import requests

# Lyrikecke hat anscheinend die Form-Inputs geändert.
# Das Formular im debug_output.html hat das Feld 'save', aber der Action Button hat name="save"
# Es gab in der Vergangenheit manchmal Probleme mit unsichtbaren Honeypots oder Token.

url = "https://www.lyrikecke.de/reimlexikon"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Content-Type": "application/x-www-form-urlencoded"
}

# Lass uns schauen ob wir eine CSRF Token im Formular übersehen haben.
import re

resp = requests.get(url, headers=headers)
html = resp.text

inputs = re.findall(r'<input[^>]*>', html)
print("Gefundene Input-Felder im Formular:")
for inp in inputs:
    print(inp)

# Und was ist mit 'select' oder 'button'?
buttons = re.findall(r'<button[^>]*>', html)
print("\nGefundene Buttons:")
for b in buttons:
    print(b)
