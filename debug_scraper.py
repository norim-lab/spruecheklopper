import requests
from reim_scraper import build_session, extract_json_from_html

session = build_session()
data = {
    "rhyme_term": "abendrot",
    "rhyme_precision": "2",
    "rhyme_limit_term": "1"
}

print("Sende POST Request...")
resp = session.post(
    "https://www.lyrikecke.de/reimlexikon",
    data=data,
    timeout=30,
    allow_redirects=True,
)

print(f"Status Code: {resp.status_code}")
print(f"Antwort-Länge: {len(resp.text)} Zeichen")

# Schauen wir uns die ersten paar hundert Zeichen und mögliche Fehlermeldungen an
if len(resp.text) < 10000:
    print("Die Antwort ist sehr kurz, hier ist der Inhalt:")
    print(resp.text[:2000])

# Teste die Extraktion manuell
bt_idx = resp.text.find("bootstrapTable")
print(f"'bootstrapTable' gefunden an Position: {bt_idx}")

if bt_idx >= 0:
    data_marker = "data: ["
    data_idx = resp.text.find(data_marker, bt_idx)
    if data_idx < 0:
        data_marker = "data:["
        data_idx = resp.text.find(data_marker, bt_idx)
    print(f"'{data_marker}' gefunden an Position: {data_idx}")
