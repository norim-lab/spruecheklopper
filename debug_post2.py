import requests

# Oh, wartet mal, die Lyrikecke Website nutzt für die POST Submission:
# <form name="rhyme" method="post" action="/reimlexikon">

# Vielleicht blockiert der Server uns, weil wir keinen Referer oder eine leere CSRF mitgeschickt haben?
# Aber er liefert einfach 200 OK mit dem Formular zurück.
# Das passiert typischerweise, wenn die Eingabe-Felder nicht stimmen!
# Schauen wir nochmal in den HTML Quelltext des Formulars:
# <input type="text" id="rhyme_term" name="rhyme_term" required="required" value="abendrot" class="form-control"/>
# ABER in unserem debug_output.html Stand:
# <input type="text" id="rhyme_term" name="rhyme_term" required="required" value="" class="form-control"/>

# Das bedeutet: Er hat das "rhyme_term" NICHT ins Formular übernommen.
# Es könnte sein, dass er POST blockiert wenn kein gültiges Cookie da ist?
# ODER wir müssen den Submit-Button Namen GENAU so schicken wie er heißt.

url = "https://www.lyrikecke.de/reimlexikon"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Content-Type": "application/x-www-form-urlencoded",
}

# Lass uns testen ob requests ein Problem mit dem dict hat und wir stattdessen einen string schicken sollten:
payload = "rhyme_term=abendrot&rhyme_precision=2&rhyme_limit_term=1&save="

resp = requests.post(url, data=payload, headers=headers)
print(len(resp.text))
if "bootstrapTable" in resp.text:
    print("GEFUNDEN mit string payload")
else:
    print("NICHT gefunden")

# Let's try with multipart/form-data
files = {
    "rhyme_term": (None, "abendrot"),
    "rhyme_precision": (None, "2"),
    "rhyme_limit_term": (None, "1"),
    "save": (None, "")
}
resp2 = requests.post(url, files=files, headers={"User-Agent": headers["User-Agent"]})
print(len(resp2.text))
if "bootstrapTable" in resp2.text:
    print("GEFUNDEN mit multipart")
else:
    print("NICHT gefunden")
