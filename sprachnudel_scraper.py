"""
sprachnudel_scraper.py
Kompletter Scrape der sprachnudel.de Reimwort-DB (~22.000 Einzelwörter).
Resume-fähig, rate-limitiert, verlustfrei.

Usage:
  python sprachnudel_scraper.py              # Normaler Lauf
  python sprachnudel_scraper.py --test 30    # Testlauf mit 30 Wörtern
  python sprachnudel_scraper.py --build      # Post-Processing: Reimgruppen bauen

Bugs gefixt vs. Vorgängerversion:
  1. _2/_3-Dedup war broken (base berechnet aber nicht benutzt)
  2. Suchwort selbst fehlte in Results (keine wortart/silben)
  3. Parser fing Nav-Links (kein Scope-Guard für results-Bereich)
  4. determine_klang() wurde nie in Output geschrieben
  5. Adjektive ohne <h5> → silben=None → jetzt count_silben() Fallback
  6. klang jetzt im raw-Entry enthalten
"""

import json, re, time, gzip, sys
from pathlib import Path
from urllib.parse import unquote
from html.parser import HTMLParser
import requests

BASE_URL    = "https://www.sprachnudel.de/search"
SITEMAP_URL = "https://www.sprachnudel.de/sitemap-word-0.xml.gz"
OUTPUT_DIR  = Path("output")
PROGRESS_F  = OUTPUT_DIR / "sn_progress.json"
RAW_F       = OUTPUT_DIR / "sprachnudel_raw.jsonl"
RAW_SNAPSHOT_F = OUTPUT_DIR / "sprachnudel_raw.snapshot.jsonl"
GROUPS_F    = OUTPUT_DIR / "reimgruppen_sn.json"
DELAY       = 0.8

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "de-DE,de;q=0.9",
}

SILBEN_MAP = {
    "Mit einer Silbe": 1, "Mit 2 Silben": 2, "Mit 3 Silben": 3,
    "Mit 4 Silben": 4,    "Mit 5 Silben": 5, "Mit 6 Silben": 6,
    "Mit 7 Silben": 7,    "Mit 8 Silben": 8, "Mit 9 Silben": 9,
}

RESULT_ANCHOR_CLASSES = {"mb-2", "ms-2"}


def count_silben(wort: str) -> int:
    vokale = "aeiouäöüy"
    count, prev_vowel = 0, False
    for ch in wort.lower():
        is_v = ch in vokale
        if is_v and not prev_vowel:
            count += 1
        prev_vowel = is_v
    return max(1, count)


def get_klang(wort: str) -> str:
    w = wort.lower().strip()
    vokale = "aeiouäöüy"
    for i in range(len(w) - 1, -1, -1):
        if w[i] in vokale:
            return w[i:]
    return w[-3:] if len(w) >= 3 else w


class RhymeParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results: list[dict] = []
        self.count: int = 0

        self._wortart = None
        self._silben  = None

        self._in_h1   = False
        self._in_h4   = False
        self._in_h5   = False
        self._in_li_result = False
        self._in_a    = False
        self._a_text  = ""
        self._h1_buf  = ""

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        classes = set(attr_dict.get("class", "").split())

        if tag == "h1":
            self._in_h1 = True
        elif tag == "h4":
            self._in_h4 = True
        elif tag == "h5":
            self._in_h5 = True
        elif tag == "li":
            if classes & RESULT_ANCHOR_CLASSES:
                self._in_li_result = True
        elif tag == "a" and self._in_li_result:
            href = attr_dict.get("href", "")
            if "/woerterbuch/" in href:
                self._in_a   = True
                self._a_text = ""

    def handle_endtag(self, tag):
        if tag == "h1":
            self._in_h1 = False
            m = re.search(r'(\d+)\s+Reimw', self._h1_buf)
            if m:
                self.count = int(m.group(1))
        elif tag == "h4":
            self._in_h4 = False
        elif tag == "h5":
            self._in_h5 = False
        elif tag == "li":
            self._in_li_result = False
        elif tag == "a" and self._in_a:
            self._in_a = False
            wort = self._a_text.strip()
            if wort and self._wortart:
                silben = self._silben if self._silben else count_silben(wort)
                self.results.append({
                    "wort":    wort,
                    "wortart": self._wortart.lower(),
                    "silben":  silben,
                })

    def handle_data(self, data):
        d = data.strip()
        if not d:
            return

        if self._in_h1:
            self._h1_buf += d
        if self._in_h4:
            self._wortart = d
            self._silben  = None
        if self._in_h5:
            self._silben = SILBEN_MAP.get(d)
        if self._in_a:
            self._a_text += data


def load_sitemap() -> list[str]:
    print("Lade Sitemap...")
    r = requests.get(SITEMAP_URL, headers=HEADERS, timeout=30)
    raw_xml = gzip.decompress(r.content).decode("utf-8")

    urls = re.findall(
        r'<loc>https://www\.sprachnudel\.de/woerterbuch/(.*?)</loc>',
        raw_xml
    )
    print(f"  {len(urls)} URLs in Sitemap")

    words = []
    seen_lower = set()

    for u in urls:
        decoded = unquote(u).strip()

        if "-" in decoded:
            continue

        base = re.sub(r'_\d+$', '', decoded)

        key = base.lower()
        if key in seen_lower:
            continue
        seen_lower.add(key)

        words.append(base)

    print(f"  → {len(words)} eindeutige Einzelwörter nach Filter")
    return words


def load_progress() -> set:
    if PROGRESS_F.exists():
        with open(PROGRESS_F, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_progress(done: set):
    with open(PROGRESS_F, "w", encoding="utf-8") as f:
        json.dump(sorted(done), f, ensure_ascii=False)


def scrape(test_n: int = 0):
    OUTPUT_DIR.mkdir(exist_ok=True)

    done  = load_progress()
    words = load_sitemap()
    todo  = [w for w in words if w not in done]

    if test_n:
        todo = todo[:test_n]
        print(f"TESTLAUF: {test_n} Wörter")

    print(f"Bereits erledigt: {len(done)} | Zu scrapen: {len(todo)}")
    if not todo:
        print("Nichts zu tun!")
        return

    session = requests.Session()
    session.headers.update(HEADERS)

    consecutive_errors = 0
    last_count = 0
    t0 = time.time()

    with open(RAW_F, "a", encoding="utf-8") as raw_out:
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
                        consecutive_errors += 1
                        print(f"  [{i+1}/{len(todo)}] {word!r}: NETZWERK-FEHLER {e}")
                        if attempt < 4:
                            time.sleep(10 * (attempt + 1))
                            continue
                        break

                    if r.status_code == 404:
                        done.add(word)
                        consecutive_errors = 0
                        success = True
                        break

                    if r.status_code == 429:
                        wait = 30 * (attempt + 1)
                        print(f"  [{i+1}/{len(todo)}] {word!r}: RATE LIMIT – warte {wait}s (Versuch {attempt+1}/5)")
                        time.sleep(wait)
                        continue

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

                    suchwort_entry = {
                        "wort":    word,
                        "wortart": "suchwort",
                        "silben":  count_silben(word),
                    }

                    entry = {
                        "suchwort": word,
                        "klang":    get_klang(word),
                        "count":    parser.count,
                        "suchwort_silben": count_silben(word),
                        "results":  [suchwort_entry] + parser.results,
                    }

                    raw_out.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    raw_out.flush()

                    done.add(word)
                    consecutive_errors = 0
                    success = True
                    break

                if not success:
                    consecutive_errors += 1
                    if consecutive_errors >= 50:
                        print("Zu viele aufeinanderfolgende Fehler – Stoppe.")
                        break

                if (i + 1) % 100 == 0:
                    save_progress(done)
                    elapsed = time.time() - t0
                    rate    = (i + 1) / elapsed
                    eta_min = (len(todo) - i - 1) / rate / 60
                    print(
                        f"  [{i+1:>5}/{len(todo)}] {word:<20} "
                        f"| {last_count:>3} Reime "
                        f"| {rate:.1f} req/s "
                        f"| ETA {eta_min:.0f}min"
                    )

                time.sleep(DELAY)

            except KeyboardInterrupt:
                print(f"\nAbgebrochen bei {word!r} – speichere Fortschritt...")
                break
            except Exception as e:
                print(f"  [{i+1}/{len(todo)}] {word!r}: FEHLER {type(e).__name__}: {e}")
                consecutive_errors += 1
                if consecutive_errors >= 10:
                    print("Zu viele Fehler – Stoppe.")
                    break
                time.sleep(5)
    save_progress(done)

    elapsed = time.time() - t0
    total   = len(done)
    print(f"\nFertig! {total} Wörter in {elapsed/60:.1f}min → {RAW_F}")


def build_reimgruppen():
    source_file = RAW_SNAPSHOT_F if RAW_SNAPSHOT_F.exists() else RAW_F
    if not source_file.exists():
        print(f"Keine Rohdaten unter {source_file}")
        return

    print(f"Baue Reimgruppen aus {source_file}...")

    groups: dict[tuple, set] = {}

    with open(source_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            klang = entry.get("klang") or get_klang(entry["suchwort"])

            for r in entry.get("results", []):
                wort    = r["wort"]
                wortart = r["wortart"]
                silben  = r["silben"]

                if wortart == "suchwort":
                    wortart = "unbekannt"

                key = (klang, silben, wortart)
                if key not in groups:
                    groups[key] = set()
                groups[key].add(wort)

    output = []
    for (klang, silben, wortart), woerter in sorted(groups.items()):
        output.append({
            "klang":   klang,
            "silben":  silben,
            "wortart": wortart,
            "wörter":  sorted(woerter),
            "anzahl":  len(woerter),
        })

    with open(GROUPS_F, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    total_woerter = sum(g["anzahl"] for g in output)
    print(f"  {len(output)} Reimgruppen mit {total_woerter} Wörtern → {GROUPS_F}")
    print(f"  Beispiel (erste 5):")
    for g in output[:5]:
        print(f"    -{g['klang']} / {g['silben']}sil / {g['wortart']}: {g['wörter'][:8]}...")


SILBEN_LABEL = {
    1: "Mit einer Silbe",
    2: "Mit 2 Silben",
    3: "Mit 3 Silben",
    4: "Mit 4 Silben",
    5: "Mit 5 Silben",
    6: "Mit 6 Silben",
    7: "Mit 7 Silben",
    8: "Mit 8 Silben",
    9: "Mit 9 Silben",
}

WORTART_ORDER = ["allgemein", "adjektive", "substantive", "verben", "unbekannt"]


def show_rhymes(wort: str):
    if not GROUPS_F.exists():
        print("Reimgruppen noch nicht gebaut. Bitte erst --build ausführen.")
        return

    klang = get_klang(wort)
    with open(GROUPS_F, encoding="utf-8") as f:
        data = json.load(f)

    matching = [g for g in data if g["klang"] == klang]
    if not matching:
        print(f'Für "{wort}" wurden keine Reimwörter gefunden (Klang: -{klang}).')
        return

    total = sum(g["anzahl"] for g in matching)
    print(f'Für "{wort}" wurden {total} Reimwörter gefunden.')
    print(f"Suchtreffer im Reimwörterbuch")
    print()

    by_wortart: dict[str, list[dict]] = {}
    for g in matching:
        wa = g["wortart"]
        by_wortart.setdefault(wa, []).append(g)

    for wa in WORTART_ORDER:
        if wa not in by_wortart:
            continue
        groups = sorted(by_wortart[wa], key=lambda g: g["silben"])

        wa_label = wa.capitalize()
        print(f"{wa_label}")

        for g in groups:
            silben = g["silben"]
            silben_label = SILBEN_LABEL.get(silben, f"Mit {silben} Silben")
            print(f"  {silben_label}")
            for w in g["wörter"]:
                print(f"    {w}")
        print()


if __name__ == "__main__":
    if "--build" in sys.argv:
        build_reimgruppen()
    elif "--show" in sys.argv:
        idx = sys.argv.index("--show")
        query = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
        if not query:
            print("Usage: python sprachnudel_scraper.py --show <wort>")
        else:
            show_rhymes(query)
    else:
        test_n = 0
        if "--test" in sys.argv:
            idx = sys.argv.index("--test")
            test_n = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else 30
        scrape(test_n=test_n)
