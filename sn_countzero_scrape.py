import json
import time
import os
import sys
import random
import asyncio
import threading
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

sys.path.insert(0, str(Path(__file__).parent))
from sn_complete_scrape import (
    RhymeParser, build_entry, count_silben, get_klang,
    OUTPUT_DIR, BASE_URL,
)

CONTROL_FILE = OUTPUT_DIR / "sn_countzero_control.json"
PROGRESS_FILE = OUTPUT_DIR / "sn_countzero_progress.json"
PATCH_FILE = OUTPUT_DIR / "sprachnudel_countzero_patch.jsonl"
WORDLIST_FILE = OUTPUT_DIR / "count_zero_words.json"

SPEED_CONFIGS = {
    "slow":   {"sleep": (2.0, 4.0), "workers": 1},
    "normal": {"sleep": (1.0, 2.5), "workers": 1},
    "fast":   {"sleep": (0.5, 1.5), "workers": 1},
    "ultra":  {"sleep": (0.05, 0.15), "workers": 5},
    "vpn4":   {"sleep": (0.3, 0.8), "workers": 4, "vpn_rotate_every": 50},
}

loop = None
browser = None
challenge_start = None
recent_outcomes = []
recent_window_size = 50
stats_lock = threading.Lock()


def atomic_replace(src: Path, dst: Path, retries: int = 5, delay: float = 0.3):
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


def normalize_speed(speed: str | None) -> str:
    return speed if speed in SPEED_CONFIGS else "normal"


def current_speed() -> str:
    return normalize_speed(read_control().get("speed"))


def write_control(status: str, msg: str = "", speed: str | None = None, pid: int | None = None):
    if speed is None:
        speed = current_speed()
    if pid is None:
        pid = os.getpid()
    existing_mode = "browser"
    if CONTROL_FILE.exists():
        try:
            with open(CONTROL_FILE, "r", encoding="utf-8") as f:
                existing_mode = json.load(f).get("mode", "browser")
        except Exception:
            pass
    data = {
        "status": status,
        "msg": msg,
        "pid": pid,
        "ts": time.time(),
        "speed": normalize_speed(speed),
        "mode": existing_mode,
    }
    tmp = CONTROL_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    atomic_replace(tmp, CONTROL_FILE)


def read_control() -> dict:
    if not CONTROL_FILE.exists():
        return {"status": "idle", "msg": "", "pid": 0, "ts": 0, "speed": "normal"}
    try:
        with open(CONTROL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["speed"] = normalize_speed(data.get("speed"))
        return data
    except Exception:
        return {"status": "idle", "msg": "", "pid": 0, "ts": 0, "speed": "normal"}


def save_progress(stats: dict, patch_entries: dict, dirty_keys: set | None = None):
    history = []
    if PROGRESS_FILE.exists():
        try:
            old = json.load(open(PROGRESS_FILE, "r", encoding="utf-8"))
            history = old.get("history", [])
        except Exception:
            pass
    snapshot = {
        "ts": time.time(),
        "completed": stats["completed"],
        "found": stats["found"],
        "still_empty": stats["still_empty"],
        "blocked": stats["blocked"],
        "errors": stats["errors"],
    }
    cutoff = time.time() - 1800
    history = [h for h in history if h.get("ts", 0) > cutoff]
    history.append(snapshot)
    if len(history) > 60:
        history[-60:]

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
        atomic_replace(tmp, PATCH_FILE)

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
        "history": history,
    }
    prog_tmp = PROGRESS_FILE.with_suffix(".tmp")
    with open(prog_tmp, "w", encoding="utf-8") as f:
        json.dump(prog, f, ensure_ascii=False)
    atomic_replace(prog_tmp, PROGRESS_FILE)


def check_pause_or_stop():
    ctrl = read_control()
    cmd = ctrl.get("status", "")
    if cmd == "stop":
        return "stop"
    if cmd == "pause":
        return "pause"
    return None


def wait_while_paused():
    while True:
        ctrl = read_control()
        cmd = ctrl.get("status", "")
        if cmd == "stop":
            return "stop"
        if cmd == "pause":
            time.sleep(2)
            continue
        return "resume"


def is_challenge(text: str) -> bool:
    return "Just a moment" in text or "Nur einen Moment" in text or "cf-chl-container" in text or "Checking your browser" in text

def is_blocked_page(text: str) -> bool:
    """Erkennt Blockierungs-Seiten (IP-Ban, Rate-Limit, etc.)."""
    markers = [
        "you have been blocked",
        "You have been blocked",
        "access denied",
        "Access Denied",
        "Your IP has been blocked",
        "rate limit",
        "too many requests",
        "Error 1020",
        "error 1015",
        "Ray ID",
    ]
    low = text.lower()
    return any(m.lower() in low for m in markers)


async def init_browser():
    global browser
    if browser is not None:
        return
    print("Starte Browser (sichtbar)...")
    import nodriver as uc
    browser = await uc.start(headless=False)
    # Erste Seite öffnen (wird von nodriver automatisch erstellt)
    await asyncio.sleep(3)
    print("Browser bereit!")


def close_browser():
    global browser
    if browser is not None:
        try:
            if hasattr(browser, 'stop'):
                browser.stop()
            elif hasattr(browser, 'quit'):
                browser.quit()
        except Exception:
            pass
        browser = None


async def fetch_word(word: str, own_browser=None) -> tuple:
    """Fetched ein Wort. Bei own_browser wird dieser Tab genutzt (parallel), sonst global."""
    global challenge_start, recent_outcomes, browser

    b = own_browser if own_browser is not None else browser
    tab = None
    try:
        url = f"{BASE_URL}?term={word}&type=rhyme&extended=1"
        tab = await b.get(url)

        start = time.time()
        while time.time() - start < 60:
            text = await tab.get_content()
            if not is_challenge(text):
                break
            if challenge_start is None:
                challenge_start = time.time()
                print(f"  [CHALLENGE] Cloudflare erkannt...", flush=True)
            await asyncio.sleep(2)
        else:
            elapsed = time.time() - start
            recent_outcomes.append("blocked")
            if len(recent_outcomes) > recent_window_size * 2:
                recent_outcomes[:] = recent_outcomes[-recent_window_size:]
            return word, "blocked", None, f"Challenge nicht geloest nach {elapsed:.0f}s"

        if challenge_start is not None:
            elapsed = time.time() - challenge_start
            print(f"  [CHALLENGE] Geloest nach {elapsed:.0f}s!", flush=True)
            challenge_start = None

        text = await tab.get_content()
        p = RhymeParser()
        p.feed(text)

        # Block-Erkennung: Seite sagt "you have been blocked" etc.
        if is_blocked_page(text):
            recent_outcomes.append("blocked")
            if len(recent_outcomes) > recent_window_size * 2:
                recent_outcomes[:] = recent_outcomes[-recent_window_size:]
            return word, "blocked", None, "IP geblockt (Block-Seite erkannt)"

        entry = build_entry(word, p)

        recent_outcomes.append("ok")
        if len(recent_outcomes) > recent_window_size * 2:
            recent_outcomes[:] = recent_outcomes[-recent_window_size:]

        cnt = int(entry.get("count", 0) or 0)
        return word, "ok", entry, f"{cnt} Treffer"

    except Exception as exc:
        recent_outcomes.append("error")
        if len(recent_outcomes) > recent_window_size * 2:
            recent_outcomes[:] = recent_outcomes[-recent_window_size:]
        return word, "error", None, f"{type(exc).__name__}: {exc}"
    finally:
        pass  # Tabs nicht schliessen — nodriver verwaltet sie selbst


async def run_scrape():
    global challenge_start, recent_outcomes

    print("=== Count=0 Rescrape — BROWSER-MODUS (nodriver) ===")

    speed = current_speed()
    num_workers = SPEED_CONFIGS[speed].get("workers", 1)

    if num_workers <= 1:
        await init_browser()

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
        write_control("done", f"FERTIG: alles bereits gescraped, {found_so_far} mit Reimen")
        close_browser()
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
    dirty_keys = set()

    cfg = SPEED_CONFIGS[speed]
    sleep_seconds = cfg["sleep"]
    num_workers = cfg.get("workers", 1)

    mode_label = f"BROWSER({'ULTRA x'+str(num_workers) if num_workers > 1 else 'seq'})"
    write_control("running", f"{mode_label}: {total_words} Woerter", speed=speed)

    if num_workers > 1:
        await _run_parallel(remaining, stats, patch_entries, dirty_keys, num_workers, speed, mode_label)
    else:
        await _run_sequential(remaining, stats, patch_entries, dirty_keys, speed, mode_label)

    close_browser()
    write_control(
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


async def _run_sequential(remaining, stats, patch_entries, dirty_keys, speed, mode_label):
    """Sequentieller Modus (1 Tab) — Original-Logik."""
    cfg = SPEED_CONFIGS[speed]
    sleep_seconds = cfg["sleep"]
    write_control("running", f"{mode_label}: {stats['completed']}/{stats['total']}", speed=speed)

    idx = 0
    batch_num = 0
    while idx < len(remaining):
        cmd = check_pause_or_stop()
        if cmd == "stop":
            try:
                save_progress(stats, patch_entries)
            except Exception as e:
                print(f"WARN: Final save fehlgeschlagen: {e}", flush=True)
            write_control("stopped", f"Angehalten bei {stats['completed']}/{stats['total']}")
            print(f"\nGESTOPPT bei {stats['completed']}/{stats['total']}")
            return
        if cmd == "pause":
            write_control("paused", f"Pausiert bei {stats['completed']}/{stats['total']}")
            try:
                save_progress(stats, patch_entries)
            except Exception:
                pass
            action = wait_while_paused()
            if action == "stop":
                write_control("stopped", "Gestoppt nach Pause")
                return
            speed = current_speed()
            cfg = SPEED_CONFIGS[speed]
            sleep_seconds = cfg["sleep"]
            write_control("running", "Fortgesetzt", speed=speed)

        ctrl = read_control()
        speed = normalize_speed(ctrl.get("speed"))
        cfg = SPEED_CONFIGS[speed]
        sleep_seconds = cfg["sleep"]

        if len(recent_outcomes) >= recent_window_size:
            block_count = sum(1 for o in recent_outcomes if o == "blocked")
            block_rate = block_count / len(recent_outcomes)
            if block_rate > 0.5:
                print(f"\n!!! CIRCUIT BREAKER: {block_rate:.0%} Block-Rate!", flush=True)
                try:
                    save_progress(stats, patch_entries, dirty_keys)
                    dirty_keys.clear()
                except Exception:
                    pass
                write_control("paused", f"CIRCUIT BREAKER: {block_rate:.0%} Block-Rate - pausiert.")
                await asyncio.sleep(30)
                recent_outcomes.clear()
                write_control("running", "Fortgesetzt nach Circuit Breaker", speed=speed)

        write_control("running", f"{mode_label}: {stats['completed']}/{stats['total']}", speed=speed)

        word = remaining[idx]
        idx += 1

        if idx % 5 == 1:
            print(f"  [{stats['completed']}/{stats['total']}] Nächster: {word['wort']}", flush=True)

        retry_count = 0
        max_retries = 3
        while retry_count < max_retries:
            w, outcome, entry, msg = await fetch_word(word["wort"])
            if outcome != "error":
                break
            retry_count += 1
            if retry_count < max_retries:
                await asyncio.sleep(2 * retry_count)

        n = stats["completed"] + stats["errors"] + stats["blocked"] + 1
        print(f"  [{n}/{stats['total']}] {w}: {outcome} — {msg}" + (f" (retry {retry_count}x)" if retry_count > 0 else ""), flush=True)

        _process_result(w, outcome, entry, stats, patch_entries, dirty_keys, completed_set=None)

        batch_num += 1

        if batch_num % 10 == 0:
            _print_batch_stats(stats, batch_num)

        if batch_num % 10 == 0:
            try:
                save_progress(stats, patch_entries, dirty_keys)
                dirty_keys.clear()
            except Exception as save_err:
                print(f"WARN: Save fehlgeschlagen: {save_err}", flush=True)

        await asyncio.sleep(random.uniform(*sleep_seconds))

    try:
        save_progress(stats, patch_entries)
    except Exception as e:
        print(f"WARN: Finaler Save fehlgeschlagen: {e}", flush=True)


async def _run_parallel(remaining, stats, patch_entries, dirty_keys, num_workers, speed, mode_label):
    """Paralleler Modus (N Browser-Instanzen gleichzeitig) — ULTRA / VPN4."""

    idx = 0
    batch_num = 0
    lock = asyncio.Lock()
    stopped = False
    browsers = []
    vpn_rotate_every = SPEED_CONFIGS[speed].get("vpn_rotate_every", 0)
    vpn_since_rotate = 0

    # VPN verbinden falls vpn_rotate_every gesetzt
    if vpn_rotate_every:
        try:
            import surfshark_vpn
            vstatus = surfshark_vpn.get_status()
            if not vstatus.get("connected"):
                print("VPN: Verbinde...", flush=True)
                res = surfshark_vpn.connect()
                if res.get("ok"):
                    ip = surfshark_vpn.get_current_ip()
                    print(f"VPN: Verbunden — IP: {ip}", flush=True)
                else:
                    print(f"VPN-WARN: Verbindung fehlgeschlagen: {res.get('error')}", flush=True)
            else:
                ip = surfshark_vpn.get_current_ip()
                print(f"VPN: Bereits verbunden — IP: {ip}", flush=True)
        except Exception as e:
            print(f"VPN-WARN: {e}", flush=True)

    mode_label = f"BROWSER({'VPN4 x'+str(num_workers) if vpn_rotate_every else 'ULTRA x'+str(num_workers)})"
    write_control("running", f"{mode_label}: {stats['completed']}/{stats['total']}", speed=speed)

    # Browser-Pool starten
    import nodriver as uc
    print(f"Starte {num_workers} Browser-Instanzen...", flush=True)
    for i in range(num_workers):
        try:
            b = await uc.start(headless=False)
            browsers.append(b)
            await asyncio.sleep(0.5)
        except Exception as e:
            print(f"WARN: Browser {i} konnte nicht gestartet werden: {e}", flush=True)
    actual_workers = len(browsers)
    print(f"{actual_workers}/{num_workers} Browser gestartet", flush=True)

    if actual_workers == 0:
        print("FEHLER: Keine Browser-Instanz gestartet!", flush=True)
        return

    async def worker(worker_id: int, own_browser):
        nonlocal idx, batch_num, stopped, vpn_since_rotate
        while True:
            # Prüfe Steuerbefehle
            cmd = check_pause_or_stop()
            if cmd == "stop":
                stopped = True
                return
            if cmd == "pause":
                write_control("paused", f"Pausiert bei {stats['completed']}/{stats['total']}")
                async with lock:
                    try:
                        save_progress(stats, patch_entries, dirty_keys)
                        dirty_keys.clear()
                    except Exception:
                        pass
                action = wait_while_paused()
                if action == "stop":
                    stopped = True
                    return
                write_control("running", "Fortgesetzt", speed=speed)

            # Nächstes Wort holen
            async with lock:
                if stopped or idx >= len(remaining):
                    return
                current_speed_val = normalize_speed(read_control().get("speed"))
                local_idx = idx
                idx += 1
                word = remaining[local_idx]

            # Circuit Breaker prüfen
            if len(recent_outcomes) >= recent_window_size:
                block_count = sum(1 for o in recent_outcomes if o == "blocked")
                block_rate = block_count / len(recent_outcomes)
                if block_rate > 0.5:
                    async with lock:
                        print(f"\n!!! CIRCUIT BREAKER: {block_rate:.0%} Block-Rate! (Worker {worker_id})", flush=True)
                        try:
                            save_progress(stats, patch_entries, dirty_keys)
                            dirty_keys.clear()
                        except Exception:
                            pass
                    write_control("paused", f"CIRCUIT BREAKER: {block_rate:.0%} Block-Rate - pausiert.")
                    await asyncio.sleep(30)
                    recent_outcomes.clear()
                    write_control("running", "Fortgesetzt nach Circuit Breaker", speed=current_speed_val)

            # Fetch mit eigenem Browser (mit Retry)
            retry_count = 0
            max_retries = 3
            while retry_count < max_retries:
                w, outcome, entry, msg = await fetch_word(word["wort"], own_browser=own_browser)
                if outcome == "blocked" and vpn_rotate_every:
                    # Sofort VPN rotieren bei Block
                    retry_count += 1
                    async with lock:
                        try:
                            import surfshark_vpn
                            print(f"  BLOCKED! Rotiere VPN sofort...", flush=True)
                            res = surfshark_vpn.rotate_server()
                            if res.get("ok"):
                                print(f"  VPN rotiert → {res.get('server')} (IP: {res.get('new_ip')})", flush=True)
                            else:
                                print(f"  VPN-WARN: Rotation fehlgeschlagen: {res.get('error')}", flush=True)
                        except Exception as e:
                            print(f"  VPN-WARN: {e}", flush=True)
                    if retry_count < max_retries:
                        await asyncio.sleep(5)
                        continue  # Retry mit neuer IP
                if outcome != "error":
                    break
                retry_count += 1
                if retry_count < max_retries:
                    await asyncio.sleep(2 * retry_count)

            async with lock:
                n = stats["completed"] + stats["errors"] + stats["blocked"] + 1
                print(f"  [{n}/{stats['total']}] {w}: {outcome} — {msg}" + (f" (retry {retry_count}x)" if retry_count > 0 else ""), flush=True)

                _process_result(w, outcome, entry, stats, patch_entries, dirty_keys)

                batch_num += 1
                if batch_num % 20 == 0:
                    _print_batch_stats(stats, batch_num)

                if batch_num % 20 == 0:
                    try:
                        save_progress(stats, patch_entries, dirty_keys)
                        dirty_keys.clear()
                    except Exception as save_err:
                        print(f"WARN: Save fehlgeschlagen: {save_err}", flush=True)

                # VPN-Rotation
                if vpn_rotate_every:
                    vpn_since_rotate += 1
                    if vpn_since_rotate >= vpn_rotate_every:
                        vpn_since_rotate = 0
                        try:
                            import surfshark_vpn
                            res = surfshark_vpn.rotate_server()
                            if res.get("ok"):
                                print(f"  VPN rotiert → {res.get('server')} (IP: {res.get('new_ip')})", flush=True)
                            else:
                                print(f"  VPN-WARN: Rotation fehlgeschlagen: {res.get('error')}", flush=True)
                        except Exception as e:
                            print(f"  VPN-WARN: {e}", flush=True)

                write_control("running", f"{mode_label}: {stats['completed']}/{stats['total']}", speed=current_speed_val)

            # Kurze Pause
            cur_cfg = SPEED_CONFIGS.get(current_speed_val, SPEED_CONFIGS["normal"])
            await asyncio.sleep(random.uniform(*cur_cfg["sleep"]))

    # Worker starten
    workers = [asyncio.create_task(worker(i, browsers[i])) for i in range(actual_workers)]
    await asyncio.gather(*workers)

    # Alle Browser schliessen
    for b in browsers:
        try:
            if hasattr(b, 'stop'):
                b.stop()
            elif hasattr(b, 'quit'):
                b.quit()
        except Exception:
            pass

    if stopped:
        try:
            save_progress(stats, patch_entries)
        except Exception:
            pass
        write_control("stopped", f"Angehalten bei {stats['completed']}/{stats['total']}")
    else:
        try:
            save_progress(stats, patch_entries)
        except Exception as e:
            print(f"WARN: Finaler Save fehlgeschlagen: {e}", flush=True)


def _process_result(w, outcome, entry, stats, patch_entries, dirty_keys, completed_set=None):
    """Verarbeitet das Ergebnis eines fetch_word-Aufrufs."""
    if outcome == "ok" and entry:
        cnt = int(entry.get("count", 0))
        key = w.casefold()
        patch_entries[key] = entry
        dirty_keys.add(key)
        stats["completed"] = len(patch_entries)
        if cnt > 0:
            stats["found"] += 1
            print(f"  *** REIM GEFUNDEN: {w} ({cnt} Reime)", flush=True)
        else:
            stats["still_empty"] += 1
    elif outcome == "blocked":
        stats["blocked"] += 1
    else:
        stats["errors"] += 1


def _print_batch_stats(stats, batch_num):
    elapsed = time.time() - stats["run_started_at"]
    run_done = max(0, stats["completed"] - stats["run_completed_base"])
    rate = run_done / elapsed if elapsed > 0 else 0
    pct = stats["completed"] * 100 / stats["total"]
    print(
        f"[{stats['completed']}/{stats['total']}] ({pct:.1f}%) "
        f"found={stats['found']} empty={stats['still_empty']} "
        f"blocked={stats['blocked']} err={stats['errors']} "
        f"rate={rate:.3f}/s",
        flush=True,
    )


def main():
    global loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_scrape())
    finally:
        close_browser()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        close_browser()
        write_control("stopped", "Manuell gestoppt")
    except Exception as exc:
        try:
            close_browser()
            write_control("stopped", f"Crash: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        raise
