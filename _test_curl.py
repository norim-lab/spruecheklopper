from curl_cffi.requests import Session
import time

print("=== TEST: curl_cffi Session MIT Browser-Coockies ===")

session = Session(impersonate="chrome", headers={
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
})

import json
from cf_solver import get_cf_cookies
cookies = get_cf_cookies()
print(f"Cookies: {list(cookies.keys())}")

ok = 0
blocked = 0
for i in range(5):
    try:
        r = session.get(
            "https://www.sprachnudel.de/search?term=haus&type=rhyme&extended=1",
            timeout=20, cookies=cookies
        )
        if r.status_code == 200 and "Reimw" in r.text:
            status = f"OK ({len(r.text)} bytes)"
            ok += 1
        elif "Just a moment" in r.text or "Nur einen Moment" in r.text:
            status = "CF-CHALLENGE"
            blocked += 1
        else:
            status = f"BLOCK({r.status_code})"
            blocked += 1
        print(f"  #{i+1}: {status}")
    except Exception as e:
        blocked += 1
        print(f"  #{i+1}: FAIL - {type(e).__name__}: {e}")
    time.sleep(1)

print(f"\nErgebnis: {ok} OK, {blocked} Blocked")
