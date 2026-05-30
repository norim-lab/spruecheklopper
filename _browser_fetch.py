import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
import undetected_chromedriver as uc
from cf_solver import get_cf_cookies, get_ua, COOKIE_FILE
import json
import time

browser = None
page = None
is_ready = False

def wait_for_ready():
    global browser, page, is_ready
    if is_ready:
        return True
    print("Starte Browser...")
    browser = uc.Chrome()
    browser.get("about:blank")
    time.sleep(2)
    page = browser.find_element("tag name", "body")
    is_ready = True
    print("Browser bereit!")
    return True

def fetch_url(url):
    global browser, page
    wait_for_ready()
    browser.get(url)
    time.sleep(3)
    return browser.page_source

def save_current_cookies():
    global browser
    cookies = browser.get_cookies()
    cf_cookies = {}
    for c in cookies:
        name = c.get("name", "")
        if name.startswith("cf_") or name in ("XSRF-TOKEN", "sprachnudel_session", "src"):
            cf_cookies[name] = c["value"]
    data = {
        "cookies": cf_cookies,
        "user_agent": get_ua() or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        "expires": time.time() + 2700,
    }
    with open(COOKIE_FILE, "w") as f:
        json.dump(data, f)
    print(f"Cookies gespeichert: {list(cf_cookies.keys())}")

def close():
    global browser, is_ready
    is_ready = False
    if browser:
        browser.quit()
        browser = None
        print("Browser geschlossen.")

def is_challenge(text):
    return "Just a moment" in text or "Nur einen Moment" in text or "cf-chl-container" in text

def solve_challenge():
    global browser
    print("Challenge erkannt - warte auf Loesung...")
    for i in range(30):
        time.sleep(2)
        try:
            text = browser.page_source
            if not is_challenge(text):
                print(f"Challenge geloest nach {i*2+2}s!")
                save_current_cookies()
                return True
        except:
            pass
    return False

def fetch_with_challenge_handling(url):
    global browser
    text = fetch_url(url)
    if is_challenge(text):
        if solve_challenge():
            text = fetch_url(url)
    return text
