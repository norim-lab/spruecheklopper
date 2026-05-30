from curl_cffi import requests as cffi_requests
import sys, os, random, time
sys.path.insert(0, os.path.dirname(__file__))
from cf_solver import get_cf_cookies, get_ua
from sn_complete_scrape import ProxyRotator

print("=== HYBRID-TEST: CF-Cookie + Proxy ===")

cookies = get_cf_cookies()
ua = get_ua()
print(f"CF-Cookie: {len(cookies) if cookies else 0} Cookies | UA: {ua[:50]}..." if ua else "KEIN UA!")

rotator = ProxyRotator()
session = cffi_requests.Session(impersonate="chrome136")

ok = 0
blocked = 0
for i in range(10):
    proxy_url, proxy_dict = rotator.get_available_proxy()
    chrome_ver = "136"
    if ua:
        for part in ua.split(" "):
            if "Chrome/" in part:
                ver = part.split("Chrome/")[-1].split(".")[0]
                if ver.isdigit():
                    chrome_ver = ver
                    break

    headers = {
        "User-Agent": ua or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
        "Sec-Ch-Ua": f'"Chromium";v="{chrome_ver}", "Not/A)Brand";v="8", "Google Chrome";v="{chrome_ver}"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    try:
        r = session.get(
            "https://www.sprachnudel.de/search?term=haus&type=rhyme&extended=1",
            timeout=20, headers=headers, proxies=proxy_dict, cookies=cookies,
        )
        if r.status_code == 200 and "Just a moment" not in r.text and "Reimw" in r.text:
            status = f"OK ({len(r.text)} bytes)"
            ok += 1
        elif r.status_code == 200 and ("Just a moment" in r.text or "Nur einen Moment" in r.text):
            status = "CF-CHALLENGE (Cookie ungueltig?)"
            blocked += 1
        else:
            cf = "cf-mitigated" in str(r.headers.get("cf-mitigated", ""))
            status = f"BLOCK({r.status_code}) {'[CF-Challenge]' if cf else ''}"
            blocked += 1
        print(f"  #{i+1}: {status}")
    except Exception as e:
        blocked += 1
        print(f"  #{i+1}: FAIL - {type(e).__name__}")
    time.sleep(1)

total = ok + blocked
print(f"\nErgebnis: {ok} OK, {blocked} Blocked ({ok/total*100:.0f}% Erfolgsrate)")
