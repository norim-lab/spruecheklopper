import subprocess

curl_cmd = [
    'curl', '-s', 'https://www.lyrikecke.de/reimlexikon',
    '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    '-H', 'Accept: text/html',
    '-H', 'Content-Type: application/x-www-form-urlencoded',
    '--data-raw', 'rhyme_term=abendrot&rhyme_precision=2&rhyme_limit_term=1&rhyme_limit_rare=1'
]

print("Ausführen von CURL...")
result = subprocess.run(curl_cmd, capture_output=True, text=False)

try:
    text = result.stdout.decode('utf-8')
    if "bootstrapTable" in text:
        print("GEFUNDEN mit CURL (ohne gzip)!")
    else:
        print("NICHT gefunden mit CURL.")
        print(f"Länge: {len(text)}")
except Exception as e:
    print(f"Fehler: {e}")
