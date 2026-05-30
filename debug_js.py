import requests

# Oh, wartet! In der URL-Struktur gab es früher `action="/reimlexikon"`, 
# aber wenn wir auf der Startseite von Lyrikecke.de sind, ist der action Link "/reimlexikon"
# aber vielleicht ist die eigentliche Action URL eine ganz andere?

# Ein weiterer Versuch: Wenn die Seite komplett statisch lädt, 
# vielleicht verarbeitet ein Javascript das Formular und sendet es an eine API?
# Wir haben vorher debug_get_api.py versucht, und `/reimlexikon/schnellsuche` war ein 404.

# Schauen wir in die `lyrikecke_spring.css` oder `lyrikecke2.min.js`
# aus dem HTML: <script src="/js/lyrikecke2.min.js" type="text/javascript"></script>

url_js = "https://www.lyrikecke.de/js/lyrikecke2.min.js"
headers = {"User-Agent": "Mozilla/5.0"}
resp = requests.get(url_js, headers=headers)
with open("lyrikecke2.js", "w", encoding="utf-8") as f:
    f.write(resp.text)
    
print("lyrikecke2.js gespeichert.")
