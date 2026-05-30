import json, re, time, sys
from pathlib import Path
from html.parser import HTMLParser
import requests

BASE_DIR = Path(r"C:\Users\miron\Documents\trae_projects\scrape")
OUTPUT_DIR = BASE_DIR / "output"
RAW_F = OUTPUT_DIR / "sprachnudel_raw.jsonl"
PROGRESS_F = OUTPUT_DIR / "sn_fix_progress.json"
LOG_F = OUTPUT_DIR / "sn_fix_log.txt"

BASE_URL = "https://www.sprachnudel.de/search"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "de-DE,de;q=0.9",
}
DELAY = 1.0
MAX_CONSECUTIVE_ERRORS = 50
MAX_CONSECUTIVE_GENERIC_ERRORS = 10
MAX_CONSECUTIVE_403 = 20
COOLDOWN_403_SECONDS = 15 * 60
COOLDOWN_ERROR_SECONDS = 5 * 60

SILBEN_MAP = {
    "Mit einer Silbe": 1, "Mit 2 Silben": 2, "Mit 3 Silben": 3,
    "Mit 4 Silben": 4, "Mit 5 Silben": 5, "Mit 6 Silben": 6,
    "Mit 7 Silben": 7, "Mit 8 Silben": 8, "Mit 9 Silben": 9,
}
RESULT_CLASSES = {"mb-2", "ms-2"}
ALLOWED_WORTARTEN = {"allgemein", "adjektive", "substantive", "verben"}
VOWELS = set("aeiouäöüy")


def count_silben(wort):
    count, prev = 0, False
    for ch in wort.lower():
        is_v = ch in VOWELS
        if is_v and not prev:
            count += 1
        prev = is_v
    return max(1, count)


def get_klang(wort):
    w = wort.lower().strip()
    for i in range(len(w) - 1, -1, -1):
        if w[i] in VOWELS:
            return w[i:]
    return w[-3:] if len(w) >= 3 else w


class RhymeParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results = []
        self.count = 0
        self._wortart = None
        self._silben = None
        self._in_h1 = False
        self._in_h4 = False
        self._in_h5 = False
        self._in_li_result = False
        self._in_a = False
        self._a_text = ""
        self._h1_buf = ""
        self._h4_buf = ""
        self._h5_buf = ""

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        classes = set(attr_dict.get("class", "").split())
        if tag == "h1":
            self._in_h1 = True
            self._h1_buf = ""
        elif tag == "h4":
            self._in_h4 = True
            self._h4_buf = ""
        elif tag == "h5":
            self._in_h5 = True
            self._h5_buf = ""
        elif tag == "li":
            if RESULT_CLASSES.issubset(classes):
                self._in_li_result = True
        elif tag == "a" and self._in_li_result:
            href = attr_dict.get("href", "")
            if "/woerterbuch/" in href:
                self._in_a = True
                self._a_text = ""

    def handle_endtag(self, tag):
        if tag == "h1":
            self._in_h1 = False
            m = re.search(r'(\d+)\s+Reimw', self._h1_buf)
            if m:
                self.count = int(m.group(1))
        elif tag == "h4":
            self._in_h4 = False
            heading = self._h4_buf.strip().lower()
            self._wortart = heading if heading in ALLOWED_WORTARTEN else None
            self._silben = None
        elif tag == "h5":
            self._in_h5 = False
            self._silben = SILBEN_MAP.get(self._h5_buf.strip())
        elif tag == "li":
            self._in_li_result = False
        elif tag == "a" and self._in_a:
            self._in_a = False
            wort = self._a_text.strip()
            if wort and self._wortart:
                silben = self._silben if self._silben else count_silben(wort)
                self.results.append({
                    "wort": wort,
                    "wortart": self._wortart.lower(),
                    "silben": silben,
                })

    def handle_data(self, data):
        d = data.strip()
        if not d:
            return
        if self._in_h1:
            self._h1_buf += d
        if self._in_h4:
            self._h4_buf += d
        if self._in_h5:
            self._h5_buf += d
        if self._in_a:
            self._a_text += data


def load_fix_progress():
    if PROGRESS_F.exists():
        return set(json.load(open(PROGRESS_F, encoding="utf-8")))
    return set()


def save_fix_progress(done):
    with open(PROGRESS_F, "w", encoding="utf-8") as f:
        json.dump(sorted(done), f, ensure_ascii=False)


def rewrite_raw_file(best_entries):
    tmp_path = RAW_F.with_suffix(".jsonl.tmp")
    with open(tmp_path, "w", encoding="utf-8") as out:
        for suchwort in sorted(best_entries, key=str.casefold):
            out.write(json.dumps(best_entries[suchwort], ensure_ascii=False) + "\n")
    tmp_path.replace(RAW_F)


def log(msg):
    timestamp = time.strftime("%H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_F, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    log("=== Sprachnudel Ergaenzungs-Scraping ===")

    log("Lade count=0 Woerter aus Rohdaten...")
    zero_words = []
    best_entries = {}
    with open(RAW_F, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            suchwort = entry["suchwort"]
            prev = best_entries.get(suchwort)
            if prev is None or entry.get("count", 0) >= prev.get("count", 0):
                best_entries[suchwort] = entry

    for suchwort, entry in best_entries.items():
        if entry.get("count", 0) == 0:
            zero_words.append(suchwort)

    log(f"  {len(zero_words)} Woerter mit count=0 gefunden")

    done = load_fix_progress()
    todo = [w for w in zero_words if w not in done]
    log(f"  Bereits nachgescrapt: {len(done)} | Offen: {len(todo)}")

    if not todo:
        log("Nichts zu tun!")
        return

    session = requests.Session()
    session.headers.update(HEADERS)

    consecutive_errors = 0
    consecutive_403 = 0
    last_count = 0
    fixed = 0
    t0 = time.time()

    for i, word in enumerate(todo):
        try:
            success = False
            for attempt in range(5):
                try:
                    r = session.get(
                        BASE_URL,
                        params={"term": word, "type": "rhyme", "extended": "1"},
                        timeout=15,
                    )
                except requests.exceptions.RequestException as e:
                    print(f"  [{i+1}/{len(todo)}] {word!r}: NETZWERK-FEHLER {e}")
                    if attempt < 4:
                        time.sleep(10 * (attempt + 1))
                        continue
                    break

                if r.status_code == 404:
                    done.add(word)
                    consecutive_errors = 0
                    consecutive_403 = 0
                    success = True
                    break

                if r.status_code == 429:
                    wait = 30 * (attempt + 1)
                    print(f"  [{i+1}/{len(todo)}] {word!r}: RATE LIMIT - warte {wait}s")
                    time.sleep(wait)
                    continue

                if r.status_code == 403:
                    consecutive_403 += 1
                    print(f"  [{i+1}/{len(todo)}] {word!r}: HTTP 403")
                    if consecutive_403 >= MAX_CONSECUTIVE_403:
                        log(
                            f"403-Serie erkannt ({consecutive_403}x) - "
                            f"warte {COOLDOWN_403_SECONDS // 60} Minuten und versuche weiter."
                        )
                        save_fix_progress(done)
                        rewrite_raw_file(best_entries)
                        time.sleep(COOLDOWN_403_SECONDS)
                        consecutive_403 = 0
                    if attempt < 4:
                        time.sleep(10 * (attempt + 1))
                        continue
                    break

                if r.status_code != 200:
                    if attempt == 0:
                        print(f"  [{i+1}/{len(todo)}] {word!r}: HTTP {r.status_code}")
                    if attempt < 4:
                        time.sleep(5)
                        continue
                    break

                parser = RhymeParser()
                parser.feed(r.text)
                last_count = parser.count

                if parser.count > 0:
                    suchwort_entry = {
                        "wort": word,
                        "wortart": "suchwort",
                        "silben": count_silben(word),
                    }
                    entry = {
                        "suchwort": word,
                        "klang": get_klang(word),
                        "count": parser.count,
                        "suchwort_silben": count_silben(word),
                        "results": [suchwort_entry] + parser.results,
                    }
                    best_entries[word] = entry
                    fixed += 1

                done.add(word)
                consecutive_errors = 0
                consecutive_403 = 0
                success = True
                break

            if not success:
                done.add(word)
                consecutive_errors += 1
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    log(
                        f"Zu viele aufeinanderfolgende Fehler ({consecutive_errors}) - "
                        f"warte {COOLDOWN_ERROR_SECONDS // 60} Minuten und setze fort."
                    )
                    save_fix_progress(done)
                    rewrite_raw_file(best_entries)
                    time.sleep(COOLDOWN_ERROR_SECONDS)
                    consecutive_errors = 0

            if (i + 1) % 100 == 0:
                save_fix_progress(done)
                elapsed = time.time() - t0
                rate = (i + 1) / elapsed
                eta_min = (len(todo) - i - 1) / rate / 60
                log(
                    f"  [{i+1:>5}/{len(todo)}] {word:<20} "
                    f"| {last_count:>3} Reime "
                    f"| {rate:.1f} req/s "
                    f"| Fixiert: {fixed} "
                    f"| ETA {eta_min:.0f}min"
                )

            time.sleep(DELAY)

        except KeyboardInterrupt:
            log(f"Abgebrochen bei {word!r} - speichere Fortschritt...")
            break
        except Exception as e:
            log(f"  [{i+1}/{len(todo)}] {word!r}: FEHLER {type(e).__name__}: {e}")
            consecutive_errors += 1
            if consecutive_errors >= MAX_CONSECUTIVE_GENERIC_ERRORS:
                log(
                    f"Zu viele Fehler ({consecutive_errors}) - "
                    f"warte {COOLDOWN_ERROR_SECONDS // 60} Minuten und setze fort."
                )
                save_fix_progress(done)
                rewrite_raw_file(best_entries)
                time.sleep(COOLDOWN_ERROR_SECONDS)
                consecutive_errors = 0
            time.sleep(5)

    rewrite_raw_file(best_entries)
    save_fix_progress(done)

    elapsed = time.time() - t0
    log(f"Fertig! {fixed} Woerter mit count=0 nachgescrapt, {len(done)} erledigt, {elapsed/60:.1f}min")
    log("Naechster Schritt: python sprachnudel_scraper.py --build")


if __name__ == "__main__":
    main()
