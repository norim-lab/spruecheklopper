import requests

# Eine letzte Möglichkeit, wie Lyrikecke die Anfrage filtert:
# Lyrikecke blockiert vielleicht den Zugriff auf Basis der IP-Adresse oder verwendet ein Cookie-basiertes Tracking,
# oder die Website hat ein Captcha hinter einem Frame, den wir nicht sehen,
# ODER (und das ist am wahrscheinlichsten) die Website hat ihre API verändert und lädt die Daten jetzt komplett anders nach.
# Lass uns Selenium oder Playwright kurz verwenden um zu sehen, was ein ECHTER Browser macht.
# Aber da wir auf Trae sind, ist Playwright vielleicht nicht direkt installiert. Wir versuchen es mit cloudscraper nochmal, aber mit GET?

# Moment, in der ersten Konversation hatte ich "test_headers.py" geschrieben und herausgefunden,
# dass Brotli ("br") das Problem war. Das haben wir gefixt ("Accept-Encoding: gzip").
# Und DANN hat es funktioniert!
# Aber jetzt funktioniert es wieder nicht.
# Das bedeutet: Lyrikecke hat entweder uns blockiert, oder etwas an ihrem Code geändert.
# Wenn es eine Blockade ist, liefert Lyrikecke anscheinend einfach immer die Startseite aus statt des Suchergebnisses!

# Test: Was passiert, wenn wir auf lyrikecke.de suchen, aber über einen Web-Proxy?
# Oder wir rufen einfach ein anderes Wort auf.
print("Test mit einem anderen Wort...")
url = "https://www.lyrikecke.de/reimlexikon"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip",
    "Content-Type": "application/x-www-form-urlencoded",
}

data = {
    "rhyme_term": "liebe",
    "rhyme_precision": "1",
    "rhyme_limit_term": "0"
}
resp = requests.post(url, data=data, headers=headers)
print(f"Status Code: {resp.status_code}")
if "bootstrapTable" in resp.text:
    print("GEFUNDEN!")
else:
    print("NICHT gefunden")
    
# Das riecht extrem nach IP Block / Rate Limit!
# Wenn wir zu viele Anfragen ohne richtige Cookies gesendet haben, gibt der Server einfach immer das leere Formular (Startseite) zurück.
