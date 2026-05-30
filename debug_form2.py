import requests

url = "https://www.lyrikecke.de/reimlexikon"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip",
}

# Vielleicht war der "rhyme_limit_rare" Checkbox der entscheidende Faktor,
# weil im originalen Formular war er NOT checked. In meinem alten Code hatte ich ihn nicht, 
# aber vielleicht MUSS er leer übertragen werden?
# Wenn Checkbox nicht geklickt, sendet Browser das Feld nicht.

# Testen wir exakt das, was der Browser sendet
data = {
    "rhyme_term": "abendrot",
    "rhyme_precision": "2",
    "rhyme_limit_term": "1", # Checked by default
    # rhyme_limit_rare: NOT checked, also nicht senden
    "save": ""
}

print("Sende Payload...")
resp = requests.post(url, data=data, headers=headers)
print(len(resp.text))

if "bootstrapTable" in resp.text:
    print("GEFUNDEN!")
else:
    print("NICHT gefunden")
    
    # Was steht stattdessen drin? Gibt es einen Hinweis auf Ban?
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(resp.text, 'html.parser')
    for alert in soup.find_all(class_="alert"):
        print("Meldung:", alert.text)
