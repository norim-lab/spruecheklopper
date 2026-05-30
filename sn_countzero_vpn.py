"""
Count=0 Rescrape — VPN/DIRECT-MODUS (curl_cffi + CF-Cookies)

Modi:
- 'vpn':    curl_cffi + CF-Cookies + automatische VPN-Rotation
- 'direct': curl_cffi + CF-Cookies, keine VPN. Bei Blockade pausiert
            der Scraper und wartet auf manuelle IP-Erneuerung (ISP-Reconnect).

Schneller als Browser-Modus, aber benötigt gültige CF-Cookies.
"""

import json
import time
import os
import sys
import random
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

from curl_cffi import requests as cffi_requests

sys.path.insert(0, str(Path(__file__).parent))
from sn_complete_scrape import (
    RhymeParser, build_entry, count_silben, get_klang,
    OUTPUT_DIR, BASE_URL,
)
from cf_solver import get_cf_cookies, get_ua

CONTROL_FILE = OUTPUT_DIR / "sn_countzero_control.json"
PROGRESS_FILE = OUTPUT_DIR / "sn_countzero_progress.json"
PATCH_FILE = OUTPUT_DIR / "sprachnudel_countzero_patch.jsonl"
WORDLIST_FILE = OUTPUT_DIR / "count_zero_words.json"

SPEED_CONFIGS = {
    "slow": {"workers": 2, "batch_size": 4, "sleep": (1.0, 2.5)},
    "normal": {"workers": 3, "batch_size": 6, "sleep": (0.5, 1.5)},
    "fast": {"workers": 4, "batch_size": 8, "sleep": (0.3, 0.8)},
}

_thread_local = threading.local()
_recent_outcomes = []
_recent_window_size = 50
_active_mode = "vpn"  # wird in main() gesetzt


def _get_mode() -> str:
    return _active_mode


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
        "mode": _get_mode(),
    }
    tmp = CONTROL_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    _atomic_replace(tmp, CONTROL_FILE)


def _read_control() -> dict:
    if not CONTROL_FILE.exists():
        return {"status": "idle", "msg": "", "pid": 0, "ts": 0, "speed": "normal", "mode": "vpn"}
    try:
        with open(CONTROL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["speed"] = _normalize_speed(data.get("speed"))
        data["mode"] = data.get("mode", "vpn")
        return data
    except Exception:
        return {"status": "idle", "msg": "", "pid": 0, "ts": 0, "speed": "normal", "mode": "vpn"}


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
        "started_at": stats.get("started_at", time.time()),
        "run_started_at": stats.get("run_started_at", time.time()),
        "run_completed_base": stats.get("run_completed_base", 0),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": _get_mode(),
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


def _get_session() -> cffi_requests.Session:
    if not hasattr(_thread_local, "session"):
        s = cffi_requests.Session(impersonate="chrome136")
        _thread_local.session = s
    return _thread_local.session


def _fetch_word(word: str) -> tuple:
    session = _get_session()
    cookies = get_cf_cookies()
    ua = get_ua()
    if not cookies:
        return word, "no_cookie", None, "Kein CF-Cookie"

    chrome_ver = "136"
    for part in ua.split(" "):
        if "Chrome/" in part:
            ver = part.split("Chrome/")[-1].split(".")[0]
            if ver.isdigit():
                chrome_ver = ver
                break

    headers = {
        "User-Agent": ua,
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
    params = {"term": word, "type": "rhyme", "extended": "1"}

    try:
        r = session.get(BASE_URL, params=params, timeout=20, headers=headers, cookies=cookies)
    except Exception as exc:
        return word, "network", None, str(exc)

    if r.status_code == 200:
        text = r.text
        if "Just a moment" in text or "Nur einen Moment" in text:
            return word, "blocked", None, "CF-Challenge erkannt"
        p = RhymeParser()
        p.feed(text)
        entry = build_entry(word, p)
        return word, "ok", entry, f"{p.count or len(p.results)} Treffer"
    if r.status_code == 404:
        entry = build_entry(word, RhymeParser())
        return word, "ok", entry, "404 -> leer"
    if r.status_code == 403:
        return word, "403", None, "HTTP 403"
    if r.status_code == 429:
        return word, "429", None, "HTTP 429"
    if r.status_code == 500:
        entry = build_entry(word, RhymeParser())
        return word, "ok", entry, "HTTP 500 -> als leer"
    return word, "http", None, f"HTTP {r.status_code}"


def _try_vpn_rotation() -> str | None:
    """
    Versucht VPN-Server zu rotieren und CF-Cookies zu erneuern.
    Gibt den neuen Server-Namen zurück, oder None bei Fehler.
    """
    print("  >> VPN-Rotation versucht...", flush=True)
    try:
        from surfshark_vpn import rotate_server, is_wireguard_installed, get_available_configs
        if not is_wireguard_installed():
            print("  >> WireGuard nicht installiert - überspringe Rotation", flush=True)
            return None
        configs = get_available_configs()
        if not configs:
            print("  >> Keine VPN-Configs in vpn_configs/ - überspringe Rotation", flush=True)
            return None
        result = rotate_server()
        if result.get("ok"):
            server = result.get("server", "unbekannt")
            new_ip = result.get("new_ip", "?")
            print(f"  >> VPN rotiert zu: {server} (IP: {new_ip})", flush=True)
            # Sessions zurücksetzen damit neue Verbindungen die neue IP nutzen
            _reset_all_sessions()
            # CF-Cookies erneuern - alte Cookies sind mit neuer IP wertlos
            from cf_solver import invalidate_cookies
            invalidate_cookies()
            print("  >> CF-Cookies invalidiert, warte auf neue...", flush=True)
            # Kurz warten damit VPN-Verbindung stabil ist
            time.sleep(3)
            # Versuche neue Cookies zu laden (falls Cache vorhanden) oder zu holen
            new_cookies = get_cf_cookies()
            if new_cookies:
                print(f"  >> Neue CF-Cookies geladen: {len(new_cookies)} Cookies", flush=True)
            else:
                print("  >> WARNUNG: Keine neuen CF-Cookies nach VPN-Rotation!", flush=True)
            return server
        else:
            print(f"  >> VPN-Rotation fehlgeschlagen: {result.get('error', 'unbekannt')}", flush=True)
            return None
    except ImportError:
        print("  >> surfshark_vpn Modul nicht gefunden - überspringe Rotation", flush=True)
        return None
    except Exception as e:
        print(f"  >> VPN-Rotation Fehler: {e}", flush=True)
        return None


def _reset_all_sessions():
    """Setzt alle Thread-Local Sessions zurück (für neue IP nach VPN-Wechsel)."""
    # Da Sessions Thread-Local sind, können wir sie nicht direkt zurücksetzen.
    # Stattdessen leeren wir die Response-Cache indem wir die Sessions als invalid markieren.
    # Der nächste Request erstellt automatisch neue Sessions.
    global _thread_local
    # In der ThreadPoolExecutor sind die Threads bereits aktiv.
    # Wir löschen die Session-Attribute der Threads.
    try:
        if hasattr(_thread_local, "session"):
            try:
                _thread_local.session.close()
            except Exception:
                pass
            delattr(_thread_local, "session")
    except Exception:
        pass


def main():
    global _active_mode
    # Mode aus Control-File lesen
    ctrl = _read_control()
    _active_mode = ctrl.get("mode", "vpn")
    if _active_mode not in ("vpn", "direct"):
        _active_mode = "vpn"

    mode_label = "VPN" if _active_mode == "vpn" else "DIRECT (ISP-Reconnect)"
    print(f"=== Count=0 Rescrape — {mode_label}-MODUS (curl_cffi + CF-Cookies) ===")

    cookies = get_cf_cookies()
    if not cookies:
        print("FEHLER: Keine CF-Cookies! Starte cf_solver.py --direct-solve")
        _write_control("stopped", "Keine CF-Cookies - cf_solver erforderlich")
        return

    print(f"CF-Cookies geladen: {len(cookies)} Cookies")

    if not WORDLIST_FILE.exists():
        print(f"FEHLER: {WORDLIST_FILE} nicht gefunden!")
        return

    with open(WORDLIST_FILE, "r", encoding="utf-8") as f:
        word_list = json.load(f)
    total_words = len(word_list)
    print(f"Sitemap-Wortliste: {total_words} Woerter")

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

    remaining = [item for item in word_list if item["wort"].casefold() not in completed_set]
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
        "started_at": time.time(),
        "run_started_at": time.time(),
        "run_completed_base": len(completed_set),
    }
    lock = threading.Lock()
    dirty_keys = set()
    consecutive_blocked = 0

    speed = _current_speed()
    cfg = SPEED_CONFIGS[speed]
    max_workers = cfg["workers"]
    sleep_seconds = cfg["sleep"]

    _write_control("running", f"{mode_label}: {total_words} Woerter, {max_workers} parallel", speed=speed)

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

        if len(_recent_outcomes) >= _recent_window_size:
            block_count = sum(1 for o in _recent_outcomes if o in ("blocked", "403", "429"))
            block_rate = block_count / len(_recent_outcomes)
            if block_rate > 0.5:
                print(f"\n!!! CIRCUIT BREAKER: {block_rate:.0%} Block-Rate in letzten {len(_recent_outcomes)} Requests!", flush=True)
                try:
                    _save_progress(stats, patch_entries, dirty_keys)
                    dirty_keys.clear()
                except Exception:
                    pass

                # VPN-Server rotieren + CF-Cookies erneuern
                if _active_mode == "direct":
                    # Direct-Modus: pausieren und auf manuelle IP-Erneuerung warten
                    print(f"\n!!! DIRECT-MODUS: Blockiert! Bitte neue IP holen (Router reconnect).", flush=True)
                    print(f"!!! Warte auf manuelle Bestätigung über Dashboard (Fortsetzen)...", flush=True)
                    _write_control("paused", f"BLOCKIERT ({block_rate:.0%}). Bitte neue IP holen (ISP-Reconnect), dann Fortsetzen.", speed=speed)
                    _recent_outcomes.clear()
                    consecutive_blocked = 0
                    # Warten bis User "Fortsetzen" klickt (was auch IP-Wechsel bestätigt)
                    action = _wait_while_paused()
                    if action == "stop":
                        try:
                            _save_progress(stats, patch_entries)
                        except Exception:
                            pass
                        _write_control("stopped", "Gestoppt nach Blockade")
                        return
                    # CF-Cookies erneuern nach IP-Wechsel
                    from cf_solver import invalidate_cookies
                    invalidate_cookies()
                    _save_progress(stats, patch_entries, dirty_keys)
                    dirty_keys.clear()
                    _write_control("running", f"DIRECT: Fortgesetzt nach IP-Wechsel, {max_workers} parallel", speed=speed)
                else:
                    rotated = _try_vpn_rotation()
                    if rotated:
                        _write_control("running", f"VPN rotiert -> {rotated}. Fortgesetzt, {max_workers} parallel", speed=speed)
                    else:
                        _write_control("paused", f"CIRCUIT BREAKER: {block_rate:.0%} Block-Rate - pausiert (30s).", speed=speed)
                        time.sleep(30)
                        _write_control("running", f"Fortgesetzt nach Circuit Breaker, {max_workers} parallel", speed=speed)

                _recent_outcomes.clear()
                consecutive_blocked = 0

        _write_control("running", f"{mode_label}: {stats['completed']}/{stats['total']} ({max_workers} parallel)", speed=speed)

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
                            patch_entries[key] = entry
                            dirty_keys.add(key)
                            completed_set.add(key)
                            stats["completed"] = len(completed_set)
                            if cnt > 0:
                                stats["found"] += 1
                                if stats["found"] <= 50:
                                    print(f"  *** REIM GEFUNDEN: {word} ({cnt} Reime)", flush=True)
                            else:
                                stats["still_empty"] += 1
                            consecutive_blocked = 0
                            _recent_outcomes.append("ok")
                        elif outcome == "blocked":
                            stats["blocked"] += 1
                            consecutive_blocked += 1
                            _recent_outcomes.append("blocked")
                        elif outcome in ("403", "429"):
                            stats["blocked"] += 1
                            consecutive_blocked += 1
                            _recent_outcomes.append("403")
                        elif outcome in ("network", "http", "no_cookie"):
                            stats["errors"] += 1
                            _recent_outcomes.append("error")
                        else:
                            stats["errors"] += 1
                            _recent_outcomes.append("error")
                        if len(_recent_outcomes) > _recent_window_size * 2:
                            del _recent_outcomes[:len(_recent_outcomes)-_recent_window_size]
                    except Exception as exc:
                        stats["errors"] += 1
                        print(f"UNEXPECTED ERROR bei {word}: {exc}", flush=True)

        if batch_num % 10 == 0:
            elapsed = time.time() - stats["run_started_at"]
            run_done = max(0, stats["completed"] - stats["run_completed_base"])
            rate = run_done / elapsed if elapsed > 0 else 0
            pct = stats["completed"] * 100 / stats["total"]
            print(
                f"[{stats['completed']}/{stats['total']}] ({pct:.1f}%) "
                f"found={stats['found']} empty={stats['still_empty']} "
                f"blocked={stats['blocked']} err={stats['errors']} "
                f"rate={rate:.2f}/s",
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

        time.sleep(random.uniform(*sleep_seconds))

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
