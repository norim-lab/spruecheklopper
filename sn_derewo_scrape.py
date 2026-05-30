import json
import time
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
    RhymeParser, build_entry, fetch_word,
    HEADERS, OUTPUT_DIR, BASE_URL,
)
from cf_solver import get_cf_cookies, get_ua, load_cached_cookies, solve_once, start_background_solver, invalidate_cookies

DEREWO_WORDS_FILE = OUTPUT_DIR / "missing_derewo_common.json"
CONTROL_FILE = OUTPUT_DIR / "sn_derewo_control.json"
PROGRESS_FILE = OUTPUT_DIR / "sn_derewo_progress.json"
PATCH_FILE = OUTPUT_DIR / "sprachnudel_derewo_patch.jsonl"
SPEED_CONFIGS = {
    "slow": {"workers": 2, "batch_size": 2, "sleep": 0.6},
    "normal": {"workers": 3, "batch_size": 6, "sleep": 0.1},
    "fast": {"workers": 4, "batch_size": 8, "sleep": 0.02},
}

_thread_local = threading.local()


def _atomic_replace(src: Path, dst: Path, retries: int = 5, delay: float = 0.3):
    import shutil
    for attempt in range(retries):
        try:
            src.replace(dst)
            return
        except PermissionError:
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
            else:
                try:
                    if dst.exists():
                        dst.unlink()
                except PermissionError:
                    pass
                shutil.move(str(src), str(dst))


def _get_session() -> requests.Session:
    if not hasattr(_thread_local, "session"):
        s = requests.Session()
        s.headers.update(HEADERS)
        _thread_local.session = s
    return _thread_local.session


def _normalize_speed(speed: str | None) -> str:
    return speed if speed in SPEED_CONFIGS else "normal"


def _current_speed() -> str:
    return _normalize_speed(_read_control().get("speed"))


def _write_control(status: str, msg: str = "", speed: str | None = None, pid: int | None = None):
    if speed is None:
        speed = _current_speed()
    if pid is None:
        pid = os.getpid()
    data = {
        "status": status,
        "msg": msg,
        "pid": pid,
        "ts": time.time(),
        "speed": _normalize_speed(speed),
    }
    tmp = CONTROL_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    _atomic_replace(tmp, CONTROL_FILE)


def _read_control() -> dict:
    if not CONTROL_FILE.exists():
        return {"status": "idle", "msg": "", "pid": 0, "ts": 0, "speed": "normal"}
    try:
        with open(CONTROL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["speed"] = _normalize_speed(data.get("speed"))
        return data
    except Exception:
        return {"status": "idle", "msg": "", "pid": 0, "ts": 0, "speed": "normal"}


def _save_progress(stats: dict, patch_entries: dict, dirty_keys: set | None = None):
    if dirty_keys and PATCH_FILE.exists():
        try:
            with open(PATCH_FILE, "a", encoding="utf-8") as f:
                for sw in dirty_keys:
                    if sw in patch_entries:
                        f.write(json.dumps(patch_entries[sw], ensure_ascii=False) + "\n")
        except PermissionError:
            pass
    else:
        tmp = PATCH_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as out:
            for sw in sorted(patch_entries, key=str.casefold):
                out.write(json.dumps(patch_entries[sw], ensure_ascii=False) + "\n")
        _atomic_replace(tmp, PATCH_FILE)

    prog = {
        "completed": stats["requests"],
        "found": stats["found"],
        "empty": stats["empty"],
        "errors": stats["errors"],
        "blocked": stats["blocked"],
        "cookie_refreshes": stats["cookie_refreshes"],
        "started_at": stats.get("started_at", time.time()),
        "run_started_at": stats.get("run_started_at", time.time()),
        "run_completed_base": stats.get("run_completed_base", 0),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    prog_tmp = PROGRESS_FILE.with_suffix(".tmp")
    with open(prog_tmp, "w", encoding="utf-8") as f:
        json.dump(prog, f, ensure_ascii=False)
    _atomic_replace(prog_tmp, PROGRESS_FILE)


def _load_words() -> list[str]:
    if not DEREWO_WORDS_FILE.exists():
        print(f"Wortliste nicht gefunden: {DEREWO_WORDS_FILE}")
        return []
    with open(DEREWO_WORDS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


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


def _fetch_word(word: str) -> tuple:
    session = _get_session()
    cookies = get_cf_cookies()
    ua = get_ua()
    if not cookies:
        return word, "no_cookie", None, "Kein CF-Cookie"
    outcome, entry, msg = fetch_word(session, word, cf_cookies=cookies, cf_ua=ua)
    return word, outcome, entry, msg


def main():
    stats = None
    patch_entries = None
    speed = "normal"
    print("=== DeReWo Ergaenzungs-Scrape (VPN/PARALLEL) ===")
    all_words = _load_words()
    print(f"Zu scrapen: {len(all_words)} Woerter")

    load_cached_cookies()

    started_at = time.time()
    previous_completed = 0
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            prog = json.load(f)
            previous_completed = int(prog.get("completed", 0))
            started_at = float(prog.get("started_at", time.time()))
        print(f"Bereits erledigt laut Progress: {previous_completed}")

    unique_words = list(dict.fromkeys(w for w in all_words))
    if len(unique_words) < len(all_words):
        print(f"Duplikate entfernt: {len(all_words)} -> {len(unique_words)}")

    cookies = get_cf_cookies()
    if cookies:
        print(f"VPN-Modus: CF-Cookie aktiv ({len(cookies)} Cookies)")
        start_background_solver(interval=1200)
    else:
        print("WARNUNG: Kein CF-Cookie! Erst cf_solver.py --direct-solve ausfuehren.")
        return

    patch_entries = {}
    found_so_far = 0
    empty_so_far = 0
    errors_so_far = 0
    blocked_so_far = 0
    refreshes_so_far = 0

    if PATCH_FILE.exists():
        with open(PATCH_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                e = json.loads(line)
                patch_entries[e["suchwort"].casefold()] = e
                if int(e.get("count", 0)) > 0:
                    found_so_far += 1
                else:
                    empty_so_far += 1
        print(f"Bestehende Patch-Eintraege: {len(patch_entries)} ({found_so_far} mit Reimen)")

    processed_words = set(patch_entries.keys())
    remaining = [w for w in unique_words if w.casefold() not in processed_words]
    print(f"Bereits erledigt laut Patch: {len(processed_words)}")
    print(f"Verbleibend: {len(remaining)}")

    if not remaining:
        print("Nichts zu tun!")
        _write_control("done", "Alle DeReWo-Woerter verarbeitet")
        return

    stats = {
        "requests": len(processed_words),
        "found": found_so_far,
        "empty": empty_so_far,
        "errors": errors_so_far,
        "blocked": blocked_so_far,
        "cookie_refreshes": refreshes_so_far,
        "started_at": started_at,
        "run_started_at": time.time(),
        "run_completed_base": len(processed_words),
    }
    lock = threading.Lock()
    dirty_keys = set()
    consecutive_blocked = 0
    refresh_failures = 0

    speed = _current_speed()
    cfg = SPEED_CONFIGS[speed]
    max_workers = cfg["workers"]
    batch_size = cfg["batch_size"]
    sleep_seconds = cfg["sleep"]

    _write_control("running", f"DeReWo Scrape: {len(unique_words)} Woerter, {max_workers} parallel", speed=speed)

    batch_num = 0
    for batch_start in range(0, len(remaining), batch_size):
        cmd = _check_pause_or_stop()
        if cmd == "stop":
            try:
                _save_progress(stats, patch_entries)
            except Exception as e:
                print(f"WARN: Final save fehlgeschlagen: {e}", flush=True)
            _write_control("stopped", f"Angehalten bei {stats['requests']}")
            print(f"\nGESTOPPT bei {stats['requests']}")
            return
        if cmd == "pause":
            _write_control("paused", f"Pausiert bei {stats['requests']}")
            try:
                _save_progress(stats, patch_entries)
            except Exception as e:
                print(f"WARN: Save waehrend Pause fehlgeschlagen: {e}", flush=True)
            action = _wait_while_paused()
            if action == "stop":
                _write_control("stopped", "Gestoppt nach Pause")
                return
            speed = _current_speed()
            cfg = SPEED_CONFIGS[speed]
            max_workers = cfg["workers"]
            batch_size = cfg["batch_size"]
            sleep_seconds = cfg["sleep"]
            _write_control("running", f"Fortgesetzt, {max_workers} parallel", speed=speed)

        ctrl = _read_control()
        speed = _normalize_speed(ctrl.get("speed"))
        cfg = SPEED_CONFIGS[speed]
        max_workers = cfg["workers"]
        batch_size = cfg["batch_size"]
        sleep_seconds = cfg["sleep"]
        _write_control("running", f"DeReWo Scrape: {len(unique_words)} Woerter, {max_workers} parallel", speed=speed)

        if not get_cf_cookies():
            print("Cookie abgelaufen! Refresh...")
            _write_control("running", "Cookie erneuern...", speed=speed)
            if solve_once():
                stats["cookie_refreshes"] += 1
                consecutive_blocked = 0
                refresh_failures = 0
                time.sleep(5)
            else:
                refresh_failures += 1
                try:
                    _save_progress(stats, patch_entries)
                except Exception:
                    pass
                _write_control("stopped", "Cookie-Refresh fehlgeschlagen - VPN/IP wechseln")
                print("STOP: Cookie-Refresh fehlgeschlagen, bitte VPN/IP wechseln.")
                return

        if consecutive_blocked >= 5:
            print(f"!!! {consecutive_blocked} aufeinanderfolgende blocked/403 - Cookie tot!")
            _write_control("running", "Cookie invalidiert (blocked-Flood), erneuere...", speed=speed)
            invalidate_cookies()
            if solve_once():
                stats["cookie_refreshes"] += 1
                consecutive_blocked = 0
                refresh_failures = 0
                time.sleep(8)
            else:
                refresh_failures += 1
                try:
                    _save_progress(stats, patch_entries)
                except Exception:
                    pass
                _write_control("stopped", "Blocked-Flood - VPN/IP wechseln")
                print("STOP: Blocked-Flood, bitte VPN/IP wechseln.")
                return

        if refresh_failures >= 2:
            try:
                _save_progress(stats, patch_entries)
            except Exception:
                pass
            _write_control("stopped", "Mehrfacher Refresh-Fehler - VPN/IP wechseln")
            print("STOP: Mehrfacher Refresh-Fehler, bitte VPN/IP wechseln.")
            return

        batch = remaining[batch_start:batch_start + batch_size]
        if not batch:
            break

        batch_num += 1

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_fetch_word, w): w for w in batch}
            for future in as_completed(futures):
                word, outcome, entry, msg = future.result()
                with lock:
                    try:
                        n = stats["requests"] + stats["errors"] + stats["blocked"] + 1

                        if n <= 10 or outcome not in ("ok",):
                            print(f"  [{n}] {word}: {outcome} - {msg}", flush=True)

                        if outcome == "ok" and entry:
                            cnt = int(entry.get("count", 0))
                            if cnt > 0:
                                stats["found"] += 1
                            else:
                                stats["empty"] += 1
                            patch_entries[word.casefold()] = entry
                            dirty_keys.add(word.casefold())
                            processed_words.add(word.casefold())
                            stats["requests"] = len(processed_words)
                            consecutive_blocked = 0
                        elif outcome == "blocked":
                            stats["blocked"] += 1
                            consecutive_blocked += 1
                            if consecutive_blocked <= 3 or consecutive_blocked % 5 == 0:
                                print(f"  [{n}] BLOCKED: {word} (consec: {consecutive_blocked})", flush=True)
                        elif outcome == "no_cookie":
                            stats["errors"] += 1
                        elif outcome in ("403", "429"):
                            stats["errors"] += 1
                            consecutive_blocked += 1
                        elif outcome in ("network", "http"):
                            stats["errors"] += 1
                    except Exception as exc:
                        stats["errors"] += 1
                        print(f"UNEXPECTED RESULT ERROR bei {word}: {exc}", flush=True)

        try:
            _save_progress(stats, patch_entries, dirty_keys)
            dirty_keys.clear()
        except Exception as save_err:
            print(f"WARN: Save fehlgeschlagen: {save_err}", flush=True)
        _write_control("running", f"DeReWo Scrape: {len(unique_words)} Woerter, {max_workers} parallel", speed=speed)

        if batch_num % 10 == 0:
            elapsed = time.time() - stats["run_started_at"]
            run_done = max(0, stats["requests"] - stats["run_completed_base"])
            rate = run_done / elapsed if elapsed > 0 else 0
            remaining_count = len(unique_words) - stats["requests"]
            eta = int(remaining_count / rate) if rate > 0 else 0
            eta_min = eta // 60
            print(
                f"[{stats['requests']}/{len(unique_words)}] "
                f"found={stats['found']} empty={stats['empty']} "
                f"blocked={stats['blocked']} err={stats['errors']} "
                f"speed={speed} rate={rate:.2f}/s eta=~{eta_min}min "
                f"patch={len(patch_entries)} cookie_ref={stats['cookie_refreshes']}",
                flush=True,
            )

        time.sleep(sleep_seconds)

    try:
        _save_progress(stats, patch_entries)
    except Exception as e:
        print(f"WARN: Finaler Save fehlgeschlagen: {e}", flush=True)
    _write_control(
        "done",
        f"FERTIG: {stats['found']} mit Reimen, {stats['empty']} leer, "
        f"{stats['blocked']} blocked, {stats['errors']} Fehler",
    )

    print(f"\nFERTIG: {stats['found']} Woerter mit Reimen, {stats['empty']} leer, {stats['blocked']} blocked")
    print(f"Patch: {len(patch_entries)} Eintraege in {PATCH_FILE}")
    print(f"Cookie-Refreshs: {stats['cookie_refreshes']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        try:
            _write_control("stopped", f"Crash: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        raise
