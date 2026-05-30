import json
import time
import os
import sys
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
import requests

sys.path.insert(0, str(Path(__file__).parent))
from sn_complete_scrape import (
    RhymeParser, build_entry, fetch_word, count_silben, get_klang,
    HEADERS, OUTPUT_DIR, BASE_URL, BFSFrontier,
)
from cf_solver import get_cf_cookies, get_ua, load_cached_cookies, solve_once, start_background_solver, invalidate_cookies

CONTROL_FILE = OUTPUT_DIR / "sn_snowball_control.json"
PROGRESS_FILE = OUTPUT_DIR / "sn_snowball_progress.json"
PATCH_FILE = OUTPUT_DIR / "sprachnudel_snowball_patch.jsonl"
FRONTIER_FILE = OUTPUT_DIR / "sn_snowball_frontier.json"
V8_FILE = OUTPUT_DIR / "sprachnudel_raw.snapshot.v8.merged.jsonl"

SPEED_CONFIGS = {
    "slow": {"workers": 2, "batch_size": 4, "sleep": 0.6},
    "normal": {"workers": 3, "batch_size": 6, "sleep": 0.1},
    "fast": {"workers": 4, "batch_size": 8, "sleep": 0.02},
}

_thread_local = threading.local()
_existing_words: set[str] = set()


def _prune_frontier(frontier: BFSFrontier, known: set[str]) -> int:
    """
    Entfernt aus der Frontier-Queue alle Woerter, die bereits bekannt/gescraped sind.
    Das verhindert, dass Snowball laeuft aber fast nur Re-Scrapes macht.
    """
    removed = 0
    with frontier._lock:
        if not frontier._queue:
            return 0
        new_q: deque[str] = deque()
        for w in list(frontier._queue):
            if str(w).casefold() in known:
                removed += 1
            else:
                new_q.append(w)
        frontier._queue = new_q
    return removed


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


def _load_existing():
    global _existing_words
    if V8_FILE.exists():
        with open(V8_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                sw = entry.get("suchwort", "").casefold()
                if sw:
                    _existing_words.add(sw)
    print(f"Bekannte Woerter (v8): {len(_existing_words)}")


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


def _save_progress(stats: dict, frontier: BFSFrontier, patch_entries: dict, dirty_keys: set | None = None):
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

    frontier.save()

    prog = {
        "completed": stats["requests"],
        "found": stats["found"],
        "empty": stats["empty"],
        "snowball_added": stats["snowball_added"],
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


def _init_frontier_from_v8(frontier: BFSFrontier, known: set[str]) -> int:
    if not V8_FILE.exists():
        print("KEIN v8 Snapshot — kann Frontier nicht initialisieren!")
        return 0
    rhyme_words = set()
    print("Lade Reimwörter aus v8 Snapshot für Frontier-Initialisierung...")
    with open(V8_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if int(entry.get("count", 0)) <= 0:
                continue
            for r in entry.get("results", []):
                rw = r.get("wort", "")
                if rw:
                    rhyme_words.add(rw)
    candidates = [w for w in rhyme_words if w.casefold() not in known]
    added = frontier.push_many(candidates)
    print(f"Frontier-Init: {len(rhyme_words)} Reimwörter gesamt, {len(candidates)} neu, {added} zur Queue hinzugefügt")
    return added


def main():
    print("=== Schneeball-Discovery-Scrape (BFSFrontier + VPN) ===")

    _load_existing()
    load_cached_cookies()

    frontier = BFSFrontier(FRONTIER_FILE)
    print(f"Frontier geladen: {len(frontier._queue)} in Queue, {len(frontier._seen)} gesehen")

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
    if PATCH_FILE.exists():
        seen_keys = set()
        with open(PATCH_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                e = json.loads(line)
                key = e["suchwort"].casefold()
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                patch_entries[key] = e
                if int(e.get("count", 0)) > 0:
                    found_so_far += 1
                else:
                    empty_so_far += 1
        print(f"Bestehende Patch-Eintraege: {len(patch_entries)} ({found_so_far} mit Reimen)")

    # WICHTIG: Frontier kann "alt" sein und massenhaft Woerter enthalten, die wir
    # bereits im Patch haben. Das macht den Lauf extrem langsam (Re-Scrape).
    known = set(_existing_words)
    known.update(patch_entries.keys())
    removed = _prune_frontier(frontier, known)
    if removed:
        print(f"Frontier bereinigt: -{removed} bereits bekannte Woerter (Queue jetzt: {len(frontier._queue)})")
        try:
            frontier.save()
        except Exception:
            pass

    if not frontier._queue:
        print("Frontier leer — initialisiere aus v8 Reimwörtern...")
        _init_frontier_from_v8(frontier, known)
        try:
            frontier.save()
        except Exception:
            pass

    if not frontier._queue:
        print("Frontier leer! Nichts zu tun.")
        _write_control("done", "Frontier leer (nach Pruning)")
        return

    previous_completed = len(patch_entries)
    stats = {
        "requests": previous_completed,
        "found": found_so_far,
        "empty": empty_so_far,
        "errors": 0,
        "blocked": 0,
        "cookie_refreshes": 0,
        "snowball_added": 0,
        "started_at": time.time(),
        "run_started_at": time.time(),
        "run_completed_base": previous_completed,
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
    total_initial = len(frontier._queue) + previous_completed

    _write_control("running", f"Schneeball: ~{total_initial} Woerter, {max_workers} parallel", speed=speed)

    batch_num = 0
    while True:
        cmd = _check_pause_or_stop()
        if cmd == "stop":
            try:
                _save_progress(stats, frontier, patch_entries)
            except Exception as e:
                print(f"WARN: Final save fehlgeschlagen: {e}", flush=True)
            _write_control("stopped", f"Angehalten bei {stats['requests']}")
            print(f"\nGESTOPPT bei {stats['requests']}")
            return
        if cmd == "pause":
            _write_control("paused", f"Pausiert bei {stats['requests']}")
            try:
                _save_progress(stats, frontier, patch_entries)
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
                    _save_progress(stats, frontier, patch_entries)
                except Exception:
                    pass
                _write_control("stopped", "Cookie-Refresh fehlgeschlagen - VPN/IP wechseln")
                print("STOP: Cookie-Refresh fehlgeschlagen, bitte VPN/IP wechseln.")
                return

        if consecutive_blocked >= 5:
            print(f"!!! {consecutive_blocked} aufeinanderfolgende blocked/403 — Cookie tot!")
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
                    _save_progress(stats, frontier, patch_entries)
                except Exception:
                    pass
                _write_control("stopped", "Blocked-Flood - VPN/IP wechseln")
                print("STOP: Blocked-Flood, bitte VPN/IP wechseln.")
                return

        if refresh_failures >= 2:
            try:
                _save_progress(stats, frontier, patch_entries)
            except Exception:
                pass
            _write_control("stopped", "Mehrfacher Refresh-Fehler - VPN/IP wechseln")
            print("STOP: Mehrfacher Refresh-Fehler, bitte VPN/IP wechseln.")
            return

        _write_control("running", f"Schneeball: ~{total_initial} Woerter, {max_workers} parallel", speed=speed)

        current_batch_size = max_workers * 4
        batch = []
        for _ in range(current_batch_size):
            word = frontier.pop()
            if word is None:
                break
            batch.append(word)

        if not batch:
            break

        batch_num += 1
        new_snowball_words = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_fetch_word, w): w for w in batch}
            for future in as_completed(futures):
                word, outcome, entry, msg = future.result()
                with lock:
                    try:
                        n = stats["requests"] + stats["errors"] + stats["blocked"] + 1

                        if n <= 10 or outcome not in ("ok",):
                            print(f"  [{n}] {word}: {outcome} — {msg}", flush=True)

                        if outcome == "ok" and entry:
                            cnt = int(entry.get("count", 0))
                            if cnt > 0:
                                stats["found"] += 1
                                for r in entry.get("results", []):
                                    rw = r.get("wort", "")
                                    if rw and rw.casefold() not in _existing_words:
                                        new_snowball_words.append(rw)
                            else:
                                stats["empty"] += 1
                            key = word.casefold()
                            patch_entries[key] = entry
                            dirty_keys.add(key)
                            _existing_words.add(key)
                            stats["requests"] = len(patch_entries)
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
                            frontier.push(word)
                        elif outcome in ("network", "http"):
                            stats["errors"] += 1
                            frontier.push(word)
                    except Exception as exc:
                        stats["errors"] += 1
                        print(f"UNEXPECTED RESULT ERROR bei {word}: {exc}", flush=True)

        if new_snowball_words:
            added = frontier.push_many(new_snowball_words)
            stats["snowball_added"] += added
            if added > 0:
                print(f"  +{added} neue Woerter zur Frontier (Queue: {len(frontier._queue)})", flush=True)

        if batch_num % 10 == 0:
            elapsed = time.time() - stats["run_started_at"]
            run_done = max(0, stats["requests"] - stats["run_completed_base"])
            rate = run_done / elapsed if elapsed > 0 else 0
            queue_len = len(frontier._queue)
            print(
                f"[{stats['requests']}] "
                f"found={stats['found']} empty={stats['empty']} "
                f"blocked={stats['blocked']} err={stats['errors']} "
                f"snowball+={stats['snowball_added']} "
                f"queue={queue_len} rate={rate:.2f}/s "
                f"patch={len(patch_entries)} cookie_ref={stats['cookie_refreshes']}",
                flush=True,
            )

        if batch_num % 10 == 0:
            try:
                _save_progress(stats, frontier, patch_entries, dirty_keys)
                dirty_keys.clear()
            except Exception as save_err:
                print(f"WARN: Save fehlgeschlagen: {save_err}", flush=True)
        else:
            try:
                if dirty_keys and PATCH_FILE.exists():
                    with open(PATCH_FILE, "a", encoding="utf-8") as f:
                        for sw in dirty_keys:
                            if sw in patch_entries:
                                f.write(json.dumps(patch_entries[sw], ensure_ascii=False) + "\n")
                    dirty_keys.clear()
            except PermissionError:
                pass

        time.sleep(sleep_seconds)

    try:
        _save_progress(stats, frontier, patch_entries)
    except Exception as e:
        print(f"WARN: Finaler Save fehlgeschlagen: {e}", flush=True)
    queue_remaining = len(frontier._queue)
    _write_control(
        "done",
        f"FERTIG: {stats['found']} mit Reimen, {stats['empty']} leer, "
        f"{stats['blocked']} blocked, {stats['snowball_added']} snowball, "
        f"queue_rest={queue_remaining}",
    )

    print(f"\nFERTIG: {stats['found']} Woerter mit Reimen, {stats['empty']} leer, {stats['blocked']} blocked")
    print(f"Schneeball neu entdeckt: {stats['snowball_added']}")
    print(f"Patch: {len(patch_entries)} Eintraege in {PATCH_FILE}")
    print(f"Frontier restlich: {queue_remaining}")
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
