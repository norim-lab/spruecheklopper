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
    RhymeParser, build_entry, fetch_word, count_silben, get_klang,
    HEADERS, OUTPUT_DIR, BASE_URL,
)
from cf_solver import get_cf_cookies, get_ua, load_cached_cookies, solve_once, start_background_solver, invalidate_cookies

CONTROL_FILE = OUTPUT_DIR / "sn_rescrape_control.json"
PROGRESS_FILE = OUTPUT_DIR / "sn_rescrape_progress.json"
PATCH_FILE = OUTPUT_DIR / "sprachnudel_rescrape_v2_patch.jsonl"
RESCRAPE_LIST_FILE = OUTPUT_DIR / "rescrape_v1_full.json"
V7_FILE = OUTPUT_DIR / "sprachnudel_raw.snapshot.v7.merged.jsonl"

SPEED_CONFIGS = {
    "slow": {"workers": 2, "batch_size": 4, "sleep": 0.6},
    "normal": {"workers": 3, "batch_size": 6, "sleep": 0.1},
    "fast": {"workers": 5, "batch_size": 10, "sleep": 0.05},
}

_thread_local = threading.local()
_bauernspruch_cats: dict[str, str] = {}


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
        "completed": stats["completed"],
        "total": stats["total"],
        "found": stats["found"],
        "still_empty": stats["still_empty"],
        "blocked": stats["blocked"],
        "errors": stats["errors"],
        "cookie_refreshes": stats["cookie_refreshes"],
        "started_at": stats.get("started_at", time.time()),
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


def _get_session() -> requests.Session:
    if not hasattr(_thread_local, "session"):
        s = requests.Session()
        s.headers.update(HEADERS)
        _thread_local.session = s
    return _thread_local.session


def _fetch_word(word: str) -> tuple:
    session = _get_session()
    cookies = get_cf_cookies()
    ua = get_ua()
    if not cookies:
        return word, "no_cookie", None, "Kein CF-Cookie"
    outcome, entry, msg = fetch_word(session, word, cf_cookies=cookies, cf_ua=ua)
    return word, outcome, entry, msg


def _load_bauernspruch_cats():
    import re
    md_path = OUTPUT_DIR / "bauernspruch_wortliste_v3_modern.md"
    if not md_path.exists():
        return
    md = md_path.read_text(encoding="utf-8")
    current_cat = ""
    for line in md.splitlines():
        m = re.match(r"^##\s+\d+\.\s+(.+)", line)
        if m:
            current_cat = m.group(1).strip()
            continue
        if not current_cat:
            continue
        if line.startswith("#") or line.startswith("---") or line.startswith("*") or line.startswith("\u2713"):
            continue
        if line.startswith("**"):
            continue
        for raw in line.split(","):
            w = raw.strip(" ,.*()[]{}\"")
            w = re.sub(r"\s*\(.*?\)\s*$", "", w)
            if not w or len(w) < 2:
                continue
            if w.startswith("#") or w.startswith("-") or w.startswith("|"):
                continue
            if not (re.match(r"^[A-Za-z\u00c4\u00d6\u00dc\u00e4\u00f6\u00fc\u00df]", w)):
                continue
            key = w.casefold()
            if key not in _bauernspruch_cats:
                _bauernspruch_cats[key] = current_cat


def main():
    print("=== Rescrape v7 — 48K verdächtige count=0 Eintraege ===")

    load_cached_cookies()
    _load_bauernspruch_cats()
    print(f"Bauernspruch-Kategorien geladen: {len(_bauernspruch_cats)} Woerter")

    cookies = get_cf_cookies()
    if cookies:
        print(f"VPN-Modus: CF-Cookie aktiv ({len(cookies)} Cookies)")
        start_background_solver(interval=1200)
    else:
        print("WARNUNG: Kein CF-Cookie! Erst cf_solver.py --direct-solve ausfuehren.")
        return

    if not RESCRAPE_LIST_FILE.exists():
        print(f"FEHLER: {RESCRAPE_LIST_FILE} nicht gefunden!")
        print("Bitte zuerst _build_rescrape_list.py ausfuehren.")
        return

    with open(RESCRAPE_LIST_FILE, "r", encoding="utf-8") as f:
        rescrape_list = json.load(f)
    total_words = len(rescrape_list)
    print(f"Rescrape-Liste: {total_words} Woerter")

    patch_entries = {}
    completed_set = set()
    found_so_far = 0
    still_empty_so_far = 0

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
                completed_set.add(key)
                if int(e.get("count", 0)) > 0:
                    found_so_far += 1
                else:
                    still_empty_so_far += 1
        print(f"Bereits gescraped: {len(patch_entries)} ({found_so_far} mit Reimen)")

    remaining = [item for item in rescrape_list if item["wort"].casefold() not in completed_set]
    print(f"Verbleibend: {len(remaining)} Woerter")

    if not remaining:
        print("Alles bereits gescraped!")
        _write_control("done", f"FERTIG: alles bereits gescraped, {found_so_far} mit Reimen")
        return

    stats = {
        "completed": len(completed_set),
        "total": total_words,
        "found": found_so_far,
        "still_empty": still_empty_so_far,
        "errors": 0,
        "blocked": 0,
        "cookie_refreshes": 0,
        "started_at": time.time(),
    }
    lock = threading.Lock()
    dirty_keys = set()
    consecutive_blocked = 0
    refresh_failures = 0

    speed = _current_speed()
    cfg = SPEED_CONFIGS[speed]
    max_workers = cfg["workers"]
    sleep_seconds = cfg["sleep"]

    _write_control("running", f"Rescrape: {total_words} Woerter, {max_workers} parallel", speed=speed)

    idx = 0
    batch_num = 0
    while idx < len(remaining):
        cmd = _check_pause_or_stop()
        if cmd == "stop":
            try:
                _save_progress(stats, patch_entries)
            except Exception as e:
                print(f"WARN: Final save fehlgeschlagen: {e}", flush=True)
            _write_control("stopped", f"Angehalten bei {stats['completed']}/{stats['total']}")
            print(f"\nGESTOPPT bei {stats['completed']}/{stats['total']}")
            return
        if cmd == "pause":
            _write_control("paused", f"Pausiert bei {stats['completed']}/{stats['total']}")
            try:
                _save_progress(stats, patch_entries)
            except Exception:
                pass
            action = _wait_while_paused()
            if action == "stop":
                _write_control("stopped", "Gestoppt nach Pause")
                return
            speed = _current_speed()
            cfg = SPEED_CONFIGS[speed]
            max_workers = cfg["workers"]
            sleep_seconds = cfg["sleep"]
            _write_control("running", f"Fortgesetzt, {max_workers} parallel", speed=speed)

        ctrl = _read_control()
        speed = _normalize_speed(ctrl.get("speed"))
        cfg = SPEED_CONFIGS[speed]
        max_workers = cfg["workers"]
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
                    _save_progress(stats, patch_entries)
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

        _write_control("running", f"Rescrape: {stats['completed']}/{stats['total']} ({max_workers} parallel)", speed=speed)

        batch_end = min(idx + max_workers * 4, len(remaining))
        batch = remaining[idx:batch_end]
        idx = batch_end

        if not batch:
            break

        batch_num += 1

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_fetch_word, item["wort"]): item for item in batch}
            for future in as_completed(futures):
                item = futures[future]
                word = item["wort"]
                kat = item.get("kategorie", "")
                try:
                    w, outcome, entry, msg = future.result()
                except Exception as exc:
                    outcome = "exception"
                    entry = None
                    msg = str(exc)

                with lock:
                    try:
                        n = stats["completed"] + stats["errors"] + stats["blocked"] + 1

                        if n <= 10 or outcome not in ("ok",):
                            print(f"  [{n}/{stats['total']}] {word}: {outcome} — {msg}", flush=True)

                        if outcome == "ok" and entry:
                            cnt = int(entry.get("count", 0))
                            key = word.casefold()
                            if kat:
                                entry["kategorie"] = kat
                            patch_entries[key] = entry
                            dirty_keys.add(key)
                            completed_set.add(key)
                            stats["completed"] = len(completed_set)
                            if cnt > 0:
                                stats["found"] += 1
                            else:
                                stats["still_empty"] += 1
                            consecutive_blocked = 0
                        elif outcome == "blocked":
                            stats["blocked"] += 1
                            consecutive_blocked += 1
                        elif outcome == "no_cookie":
                            stats["errors"] += 1
                        elif outcome in ("403", "429", "network", "http"):
                            stats["errors"] += 1
                        else:
                            stats["errors"] += 1
                    except Exception as exc:
                        stats["errors"] += 1
                        print(f"UNEXPECTED ERROR bei {word}: {exc}", flush=True)

        if batch_num % 10 == 0:
            elapsed = time.time() - stats["started_at"]
            run_done = stats["completed"]
            rate = run_done / elapsed if elapsed > 0 else 0
            pct = stats["completed"] * 100 / stats["total"]
            print(
                f"[{stats['completed']}/{stats['total']}] ({pct:.1f}%) "
                f"found={stats['found']} empty={stats['still_empty']} "
                f"blocked={stats['blocked']} err={stats['errors']} "
                f"rate={rate:.2f}/s "
                f"cookie_ref={stats['cookie_refreshes']}",
                flush=True,
            )

        if batch_num % 10 == 0:
            try:
                _save_progress(stats, patch_entries, dirty_keys)
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
        _save_progress(stats, patch_entries)
    except Exception as e:
        print(f"WARN: Finaler Save fehlgeschlagen: {e}", flush=True)

    _write_control(
        "done",
        f"FERTIG: {stats['found']} mit Reimen, {stats['still_empty']} leer, "
        f"{stats['blocked']} blocked, {stats['errors']} errors",
    )

    print(f"\nFERTIG!")
    print(f"  Mit Reimen: {stats['found']}")
    print(f"  Weiterhin leer: {stats['still_empty']}")
    print(f"  Blocked: {stats['blocked']}")
    print(f"  Fehler: {stats['errors']}")
    print(f"  Cookie-Refreshs: {stats['cookie_refreshes']}")
    print(f"  Patch: {len(patch_entries)} Eintraege in {PATCH_FILE}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        try:
            _write_control("stopped", f"Crash: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        raise
