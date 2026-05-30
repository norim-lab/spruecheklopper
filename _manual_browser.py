import asyncio
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

async def open_chrome_with_proxy():
    try:
        import undetected_chromedriver as uc
    except ImportError:
        print("undetected_chromedriver nicht installiert")
        return

    from sn_complete_scrape import ProxyRotator
    rotator = ProxyRotator()
    proxy_url, _ = rotator.get_available_proxy()
    
    TARGET_URL = "https://www.sprachnudel.de/search?term=test&type=rhyme&extended=1"
    
    print(f"Starte Chrome mit Proxy: {proxy_url[:50]}...")
    print("Proxy: ", proxy_url)
    
    options = uc.ChromeOptions()
    options.add_argument(f"--proxy-server={proxy_url}")
    
    browser = await uc.start(headless=False, options=options)
    page = await browser.get(TARGET_URL)
    
    print("\n" + "="*60)
    print("CHROME IST OFFEN!")
    print("="*60)
    print("1. Loese die Cloudflare-Challenge (Checkbox)")
    print("2. Warte bis sprachnudel.de vollstaendig geladen ist")
    print("3. Druecke STRG+C hier im Terminal wenn fertig")
    print("="*60 + "\n")
    
    try:
        while True:
            await asyncio.sleep(5)
            try:
                title = await page.title()
                print(f"Seite geladen: {title}")
            except Exception:
                print("Seite nicht verfuegbar...")
    except KeyboardInterrupt:
        print("\nExtrahiere Cookies...")
        
        try:
            all_cookies = await page.send(uc.cdp.network.get_all_cookies)
            cf_cookies = {}
            for c in all_cookies:
                if c["name"].startswith("cf_") or c["name"] in ("XSRF-TOKEN", "sprachnudel_session", "src"):
                    cf_cookies[c["name"]] = c["value"]
            
            print(f"Gefundene Cookies: {list(cf_cookies.keys())}")
            
            from cf_solver import _save_cookies, COOKIE_FILE
            import json
            from cf_solver import get_ua
            
            data = {
                "cookies": cf_cookies,
                "user_agent": await page.evaluate("navigator.userAgent"),
                "expires": 0,
            }
            
            with open(COOKIE_FILE, "w") as f:
                json.dump(data, f)
            
            print(f"Cookies gespeichert in {COOKIE_FILE}")
        except Exception as e:
            print(f"Cookie-Extraktion fehlgeschlagen: {e}")
    
    await browser.stop()
    print("Beendet.")

if __name__ == "__main__":
    asyncio.run(open_chrome_with_proxy())
