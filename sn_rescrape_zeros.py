import json
import time
import random
import os
import sys
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
import requests

sys.path.insert(0, str(Path(__file__).parent))
from sn_complete_scrape import (
    RhymeParser, AdaptiveThrottle, build_entry, fetch_word,
    LOG_FILE, HEADERS, PATCH_FILE, SNAPSHOT_V2, OUTPUT_DIR,
    BASE_URL, ProxyRotator,
)
from cf_solver import get_cf_cookies, get_ua, load_cached_cookies, solve_once, start_background_solver, invalidate_cookies

RESCRAPE_PROGRESS = OUTPUT_DIR / "sn_rescrape_progress.json"
RESCRAPE_PATCH = OUTPUT_DIR / "sprachnudel_rescrape_patch.jsonl"
CONTROL_FILE = OUTPUT_DIR / "sn_rescrape_control.json"

_thread_local = threading.local()


def _get_thread_session() -> requests.Session:
    if not hasattr(_thread_local, "session"):
        s = requests.Session()
        s.headers.update(HEADERS)
        _thread_local.session = s
    return _thread_local.session


def _write_control(status: str, msg: str = ""):
    data = {"status": status, "msg": msg, "pid": os.getpid(), "ts": time.time()}
    tmp = CONTROL_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    tmp.replace(CONTROL_FILE)


def _read_control() -> dict:
    if not CONTROL_FILE.exists():
        return {"status": "idle", "msg": "", "pid": 0, "ts": 0}
    try:
        with open(CONTROL_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"status": "idle", "msg": "", "pid": 0, "ts": 0}


def _save_progress(completed: set, started_at: float, patch_entries: dict):
    import shutil
    tmp = RESCRAPE_PATCH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as out:
        for sw in sorted(patch_entries, key=str.casefold):
            out.write(json.dumps(patch_entries[sw], ensure_ascii=False) + "\n")
    try:
        tmp.replace(RESCRAPE_PATCH)
    except PermissionError:
        try:
            if RESCRAPE_PATCH.exists():
                RESCRAPE_PATCH.unlink()
            shutil.move(str(tmp), str(RESCRAPE_PATCH))
        except Exception as e:
            print(f"Warnung: Patch-Datei konnte nicht geschrieben werden: {e}")
    prog_tmp = RESCRAPE_PROGRESS.with_suffix(".tmp")
    with open(prog_tmp, "w", encoding="utf-8") as f:
        json.dump({"completed": sorted(completed), "started_at": started_at}, f, ensure_ascii=False)
    try:
        prog_tmp.replace(RESCRAPE_PROGRESS)
    except PermissionError:
        try:
            if RESCRAPE_PROGRESS.exists():
                RESCRAPE_PROGRESS.unlink()
            shutil.move(str(prog_tmp), str(RESCRAPE_PROGRESS))
        except Exception as e:
            print(f"Warnung: Progress-Datei konnte nicht geschrieben werden: {e}")


def load_zero_count_words(snapshot_path: Path) -> list[str]:
    words = []
    with open(snapshot_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if int(entry.get("count", 0)) == 0:
                words.append(entry["suchwort"])
    return words


def _check_pause_or_stop():
    ctrl = _read_control()
    cmd = ctrl.get("status", "")
    if cmd == "stop":
        return "stop"
    if cmd == "pause":
        return "pause"
    return None


def _wait_while_paused():
    while True:
        ctrl = _read_control()
        cmd = ctrl.get("status", "")
        if cmd == "stop":
            return "stop"
        if cmd == "pause":
            time.sleep(2)
            continue
        return "resume"


def _vpn_fetch(word: str) -> tuple:
    session = _get_thread_session()
    cookies = get_cf_cookies()
    ua = get_ua()
    if not cookies:
        return word, "no_cookie", None, "Kein CF-Cookie verfuegbar", None
    outcome, entry, msg = fetch_word(session, word, cf_cookies=cookies, cf_ua=ua)
    return word, outcome, entry, msg, None


def _proxy_fetch(word: str, prx: ProxyRotator) -> tuple:
    session = _get_thread_session()
    proxy_url = None
    if prx.active:
        proxy_url, proxy_dict = prx.get_available_proxy()
        if proxy_url is None:
            return word, "all_blocked", None, "Alle Proxys geblockt", None
    outcome, entry, msg = fetch_word(session, word, prx, proxy_url=proxy_url)
    if prx.active and proxy_url:
        if outcome == "ok":
            prx.on_ok()
        elif outcome in ("403", "429"):
            prx.on_block()
        elif outcome == "network":
            prx.on_fail()
    return word, outcome, entry, msg, proxy_url


def run_test(n_words: int = 10) -> list[dict]:
    zero_words = load_zero_count_words(SNAPSHOT_V2)
    completed = set()
    if RESCRAPE_PROGRESS.exists():
        with open(RESCRAPE_PROGRESS, "r", encoding="utf-8") as f:
            completed = set(json.load(f).get("completed", []))

    candidates = [w for w in zero_words if w.casefold() not in completed]
    random.shuffle(candidates)
    test_words = candidates[:n_words]

    if not test_words:
        return [{"word": "(keine Woerter mehr)", "status": "info", "detail": "Alle count=0 Woerter wurden bereits verarbeitet."}]

    cookies = get_cf_cookies()
    if cookies:
        print(f"VPN-Modus: CF-Cookie aktiv ({len(cookies)} Cookies)")
        fetch_fn = _vpn_fetch
        fetch_args = lambda w: (w,)
    else:
        prx = ProxyRotator()
        if prx.active:
            print(f"Proxy-Modus: {len(prx.proxies)} Proxys")
            fetch_fn = _proxy_fetch
            fetch_args = lambda w: (w, prx)
        else:
            print("Weder Cookie noch Proxy - Direktverbindung")
            fetch_fn = _vpn_fetch
            fetch_args = lambda w: (w,)

    results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_fn, *fetch_args(w)): w for w in test_words}
        for future in as_completed(futures):
            word, outcome, entry, msg, _ = future.result()
            if outcome == "ok" and entry:
                cnt = int(entry.get("count", 0))
                if cnt > 0:
                    sample = [r.get("wort", "") for r in entry.get("results", [])[:5]]
                    results.append({
                        "word": word, "status": "repariert",
                        "count": cnt, "detail": f"+{cnt} Reime gefunden",
                        "sample": sample,
                    })
                else:
                    results.append({"word": word, "status": "leer", "detail": "Keine Reime auf sprachnudel.de"})
            else:
                results.append({"word": word, "status": "fehler", "detail": f"{outcome}: {msg or 'unbekannt'}"})

    return results


def main():
    print("=== Rescrape count=0 Eintraege (VPN/PARALLEL) ===")
    zero_words = load_zero_count_words(SNAPSHOT_V2)
    print(f"count=0 Woerter im Snapshot v2: {len(zero_words)}")

    completed = set()
    started_at = time.time()
    if RESCRAPE_PROGRESS.exists():
        with open(RESCRAPE_PROGRESS, "r", encoding="utf-8") as f:
            prog_data = json.load(f)
            completed = set(prog_data.get("completed", []))
            started_at = float(prog_data.get("started_at", time.time()))
        print(f"Bereits erledigt: {len(completed)}")

    remaining = [w for w in zero_words if w.casefold() not in completed]
    print(f"Verbleibend: {len(remaining)}")

    if not remaining:
        print("Nichts zu tun!")
        _write_control("done", "Alle count=0 Woerter verarbeitet")
        return

    random.shuffle(remaining)

    cookies = get_cf_cookies()
    use_vpn = bool(cookies)
    prx = None

    if use_vpn:
        print(f"VPN-Modus: CF-Cookie aktiv ({len(cookies)} Cookies)")
        print("Starte Cookie-Refresher im Hintergrund...")
        start_background_solver(interval=1200)
        fetch_fn = _vpn_fetch
    else:
        prx = ProxyRotator()
        if prx.active:
            print(f"Proxy-Modus: {len(prx.proxies)} Proxys")
            fetch_fn = lambda w: _proxy_fetch(w, prx)
        else:
            print("Kein Cookie und kein Proxy - Direktverbindung")
            fetch_fn = _vpn_fetch

    patch_entries = {}
    if RESCRAPE_PATCH.exists():
        with open(RESCRAPE_PATCH, "r", encoding="utf-8") as f:
            for line in f:
                e = json.loads(line.strip())
                patch_entries[e["suchwort"]] = e
        print(f"Bestehende Patch-Eintraege: {len(patch_entries)}")

    stats = {"requests": 0, "fixed": 0, "still_zero": 0, "errors": 0, "deferred": 0, "net_fails": 0, "cookie_refreshes": 0}
    start_time = time.time()
    lock = threading.Lock()

    max_workers = 5
    min_workers = 3
    current_workers = max_workers

    window_errors = 0
    window_total = 0
    window_size = 30
    consecutive_403 = 0

    mode = "VPN+Cookie" if use_vpn else ("Proxy" if prx and prx.active else "Direkt")
    _write_control("running", f"Gestartet: {len(remaining)} Woerter, {current_workers} parallel, Modus: {mode}")

    idx = 0

    while idx < len(remaining):
        cmd = _check_pause_or_stop()
        if cmd == "stop":
            _save_progress(completed, started_at, patch_entries)
            _write_control("stopped", f"Angehalten bei {stats['requests']}/{len(remaining)}")
            print(f"\nGESTOPPT bei {stats['requests']}/{len(remaining)}")
            return
        if cmd == "pause":
            _write_control("paused", f"Pausiert bei {stats['requests']}/{len(remaining)}")
            _save_progress(completed, started_at, patch_entries)
            print(f"\nPAUSIERT bei {stats['requests']}/{len(remaining)}")
            action = _wait_while_paused()
            if action == "stop":
                _write_control("stopped", f"Angehalten nach Pause bei {stats['requests']}/{len(remaining)}")
                print(f"\nGESTOPPT nach Pause bei {stats['requests']}/{len(remaining)}")
                return
            print(f"RESUME bei {stats['requests']}/{len(remaining)}")
            _write_control("running", f"Fortgesetzt, {current_workers} parallel, Modus: {mode}")

        if use_vpn and not get_cf_cookies():
            print("Cookie abgelaufen! Starte Refresh...")
            _write_control("running", "CF-Cookie abgelaufen, erneuere...")
            if solve_once():
                stats["cookie_refreshes"] += 1
                consecutive_403 = 0
                print("Cookie erneuert, weiter geht's!")
            else:
                print("Cookie-Refresh fehlgeschlagen! 30s Pause...")
                time.sleep(30)
                continue

        if use_vpn and consecutive_403 >= 10:
            print(f"!!! {consecutive_403} aufeinanderfolgende 403-Fehler - Cookie wahrscheinlich tot!")
            _write_control("running", "CF-Cookie invalidiert (403-Flood), erneuere...")
            invalidate_cookies()
            if solve_once():
                stats["cookie_refreshes"] += 1
                consecutive_403 = 0
                print("Cookie erneuert, weiter geht's!")
            else:
                print("Cookie-Refresh fehlgeschlagen! 60s Pause...")
                time.sleep(60)
                continue

        batch_size = current_workers * 2
        batch_end = min(idx + batch_size, len(remaining))
        batch = remaining[idx:batch_end]
        idx = batch_end

        if not batch:
            break

        with ThreadPoolExecutor(max_workers=current_workers) as executor:
            if use_vpn:
                futures = {executor.submit(fetch_fn, w): w for w in batch}
            else:
                futures = {executor.submit(fetch_fn, w): w for w in batch}
            for future in as_completed(futures):
                word, outcome, entry, msg, proxy_url = future.result()

                with lock:
                    stats["requests"] += 1
                    n = stats["requests"]

                    if outcome == "all_blocked":
                        stats["errors"] += 1
                        print(f"  [{n}] Alle Proxys geblockt - warte...", flush=True)
                        continue

                    if outcome == "no_cookie":
                        stats["errors"] += 1
                        print(f"  [{n}] Kein Cookie - muss erneuert werden", flush=True)
                        continue

                    if n <= 10 or outcome not in ("ok",):
                        src = "cookie" if use_vpn else (proxy_url.split("@")[-1][:25] if proxy_url else "direkt")
                        print(f"  [{n}] {word}: {outcome} ({src})", flush=True)

                    if outcome == "ok" and entry:
                        cnt = int(entry.get("count", 0))
                        if cnt > 0:
                            stats["fixed"] += 1
                            patch_entries[word] = entry
                        else:
                            stats["still_zero"] += 1
                        completed.add(word.casefold())
                        stats["net_fails"] = 0
                        window_errors = max(0, window_errors - 1)
                        consecutive_403 = 0
                    elif outcome in ("403", "429", "network", "http"):
                        stats["errors"] += 1
                        window_errors += 1
                        if outcome == "network":
                            stats["net_fails"] += 1
                        if outcome == "403" and use_vpn:
                            consecutive_403 += 1
                            if consecutive_403 <= 3 or consecutive_403 % 10 == 0:
                                print(f"  [{n}] 403 trotz Cookie (consecutive: {consecutive_403})", flush=True)
                        elif outcome == "403":
                            print(f"  [{n}] 403 - Proxy geblockt", flush=True)
                    elif outcome == "blocked":
                        stats["errors"] += 1
                        window_errors += 1
                        consecutive_403 += 1
                        print(f"  [{n}] {word}: BLOCKED (CF-Challenge erkannt) — Cookie tot!", flush=True)
                    else:
                        completed.add(word.casefold())
                        stats["deferred"] += 1

                    window_total += 1

        if window_total >= window_size:
            error_rate = window_errors / window_total if window_total > 0 else 0
            if error_rate > 0.30 and current_workers > min_workers:
                current_workers = max(min_workers, current_workers - 1)
                print(f"  \u26a1 Worker runter: {current_workers} (Fehlerrate: {error_rate:.0%})", flush=True)
            elif error_rate > 0.15 and current_workers > min_workers:
                current_workers = max(min_workers, current_workers - 1)
                print(f"  \u26a1 Worker runter: {current_workers} (Fehlerrate: {error_rate:.0%})", flush=True)
            elif error_rate < 0.05 and current_workers < max_workers:
                current_workers = min(max_workers, current_workers + 1)
                print(f"  \u26a1 Worker hoch: {current_workers} (Fehlerrate: {error_rate:.0%})", flush=True)
            window_errors = 0
            window_total = 0

        if stats["requests"] % 50 == 0:
            elapsed = time.time() - start_time
            rate = stats["requests"] / elapsed if elapsed > 0 else 0
            print(
                f"[{stats['requests']}/{len(remaining)}] "
                f"fixed={stats['fixed']} zero={stats['still_zero']} "
                f"err={stats['errors']} rate={rate:.2f}/s "
                f"workers={current_workers} patch={len(patch_entries)} "
                f"cookie_refresh={stats['cookie_refreshes']}",
                flush=True,
            )

        if stats["requests"] % 50 == 0:
            _save_progress(completed, started_at, patch_entries)

        time.sleep(0.15)

    _save_progress(completed, started_at, patch_entries)
    _write_control("done", f"FERTIG: {stats['fixed']} repariert, {stats['still_zero']} leer, {stats['cookie_refreshes']} Cookie-Refreshs")

    print(f"\nFERTIG: {stats['fixed']} Woerter repariert, {stats['still_zero']} bleiben leer")
    print(f"Patch: {len(patch_entries)} Eintraege in {RESCRAPE_PATCH}")
    print(f"Cookie-Refreshs: {stats['cookie_refreshes']}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        results = run_test(n)
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        main()
