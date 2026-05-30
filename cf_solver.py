import asyncio
import json
import os
import time
import threading
from pathlib import Path

BASE_DIR = Path(r"C:\Users\miron\Documents\trae_projects\scrape")
OUTPUT_DIR = BASE_DIR / "output"
COOKIE_FILE = OUTPUT_DIR / "cf_cookies.json"

TARGET_URL = "https://www.sprachnudel.de/search?term=test&type=rhyme"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"

_cookie_cache = {"cookies": {}, "user_agent": UA, "expires": 0}
_lock = threading.Lock()


def invalidate_cookies():
    with _lock:
        _cookie_cache["expires"] = 0
    print("CF-Solver: Cookies invalidiert (Force-Refresh)")


def get_cf_cookies() -> dict:
    with _lock:
        if _cookie_cache["expires"] > time.time():
            return _cookie_cache["cookies"].copy()
    if load_cached_cookies():
        with _lock:
            if _cookie_cache["expires"] > time.time():
                return _cookie_cache["cookies"].copy()
    return {}


def get_ua() -> str:
    with _lock:
        return _cookie_cache.get("user_agent", UA)


def _save_cookies(cookies: dict, user_agent: str, ttl: int = 2700):
    with _lock:
        _cookie_cache["cookies"] = cookies
        _cookie_cache["user_agent"] = user_agent
        _cookie_cache["expires"] = time.time() + ttl
    data = {"cookies": cookies, "user_agent": user_agent, "expires": _cookie_cache["expires"]}
    tmp = COOKIE_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f)
    tmp.replace(COOKIE_FILE)


def load_cached_cookies() -> bool:
    if not COOKIE_FILE.exists():
        return False
    try:
        with open(COOKIE_FILE) as f:
            data = json.load(f)
        if data.get("expires", 0) > time.time():
            with _lock:
                _cookie_cache["cookies"] = data["cookies"]
                _cookie_cache["user_agent"] = data.get("user_agent", UA)
                _cookie_cache["expires"] = data["expires"]
            print(f"CF-Solver: Gueltige Cookies geladen (laeuft ab in {int(data['expires'] - time.time())}s)")
            return True
    except Exception:
        pass
    return False


async def _solve_challenge():
    import nodriver as uc

    browser = await uc.start(headless=False, no_sandbox=True)
    page = await browser.get(TARGET_URL)

    for attempt in range(24):
        await asyncio.sleep(5)
        html = await page.get_content()
        has_challenge = "Just a moment" in html or "Nur einen Moment" in html
        if not has_challenge:
            cookies = {}
            try:
                network_cookies = await page.send(uc.cdp.network.get_all_cookies())
                cookie_list = network_cookies if isinstance(network_cookies, list) else getattr(network_cookies, 'cookies', [])
                for c in cookie_list:
                    name = getattr(c, 'name', None) or (c.get('name') if isinstance(c, dict) else None)
                    value = getattr(c, 'value', None) or (c.get('value') if isinstance(c, dict) else None)
                    if name and value:
                        cookies[name] = value
            except Exception as e:
                print(f"CF-Solver: CDP cookies Fehler: {e}")
            if not cookies:
                try:
                    js_cookies = await page.evaluate("document.cookie")
                    if js_cookies:
                        for pair in js_cookies.split(";"):
                            pair = pair.strip()
                            if "=" in pair:
                                k, v = pair.split("=", 1)
                                cookies[k.strip()] = v.strip()
                except Exception as e:
                    print(f"CF-Solver: JS cookies Fehler: {e}")
            raw_ua = await page.evaluate("navigator.userAgent")
            ua = raw_ua or UA
            _save_cookies(cookies, ua, ttl=2700)
            print(f"CF-Solver: Challenge geloest! {len(cookies)} Cookies erhalten.")
            browser.stop()
            return True
        if attempt % 4 == 0:
            print(f"CF-Solver: Warte auf Challenge-Loesung... (Versuch {attempt + 1}/24)")

    print("CF-Solver: Challenge konnte nicht geloest werden (Timeout)")
    browser.stop()
    return False


def solve_once() -> bool:
    print("CF-Solver: Starte Browser zur Challenge-Loesung (Subprocess)...")
    import subprocess
    import sys
    try:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--direct-solve"],
            timeout=180,
            capture_output=True,
            text=True,
            cwd=str(BASE_DIR),
        )
        if result.stdout:
            for line in result.stdout.strip().splitlines():
                print(f"  [cf_sub] {line}")
        if result.stderr:
            for line in result.stderr.strip().splitlines():
                if "pipe" not in line.lower() and "unclosed" not in line.lower():
                    print(f"  [cf_sub:err] {line}")
        if result.returncode == 0:
            if load_cached_cookies():
                print("CF-Solver: Subprocess erfolgreich, Cookies geladen.")
                return True
        print(f"CF-Solver: Subprocess Exit-Code {result.returncode}")
        return False
    except subprocess.TimeoutExpired:
        print("CF-Solver: Subprocess Timeout (180s)!")
        return False
    except Exception as e:
        print(f"CF-Solver: Subprocess-Fehler: {e}")
        return False


def solver_loop(interval: int = 2400):
    print(f"CF-Solver: Hintergrund-Thread gestartet (Refresh alle {interval}s)")
    while True:
        if not load_cached_cookies():
            solve_once()
        time.sleep(interval)


def start_background_solver(interval: int = 2400):
    t = threading.Thread(target=solver_loop, args=(interval,), daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    import sys
    if "--direct-solve" in sys.argv:
        # Immer ins Log-File schreiben (wichtig fuer Aufruf aus Task-Scheduler)
        log_file = OUTPUT_DIR / "cf_solver.log"
        log_file.parent.mkdir(exist_ok=True)
        sys.stdout = open(log_file, "w", buffering=1, encoding="utf-8")
        sys.stderr = sys.stdout
        print(f"CF-Solver: Starte... (PID {os.getpid()})", flush=True)
        try:
            success = asyncio.run(_solve_challenge())
            print(f"CF-Solver: Fertig, Erfolg={success}", flush=True)
        except Exception as e:
            print(f"CF-Solver FEHLER: {e}", flush=True)
            import traceback
            traceback.print_exc()
        finally:
            sys.stdout.close()
        sys.exit(0 if success else 1)
    else:
        solve_once()
        cookies = get_cf_cookies()
        print(f"Cookies: {list(cookies.keys())}")
