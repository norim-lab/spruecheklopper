import argparse
import gzip
import io
import json
import random
import re
import time
import threading
import zipfile
from collections import Counter, deque
from collections.abc import Iterable
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote
import heapq

import requests

from cf_solver import get_cf_cookies, get_ua, start_background_solver

BASE_DIR = Path(r"C:\Users\miron\Documents\trae_projects\scrape")
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

BASE_URL = "https://www.sprachnudel.de/search"
SITEMAP_URL = "https://www.sprachnudel.de/sitemap-word-0.xml.gz"
DEREWO_URL = "http://www.ids-mannheim.de/fileadmin/kl/derewo/derewo-v-ww-bll-320000g-2012-12-31-1.0.zip"
THEMA_FILE = BASE_DIR / "thematische_ergaenzung.json"

SNAPSHOT_V2 = OUTPUT_DIR / "sprachnudel_raw.snapshot.v2.jsonl"
PATCH_FILE = OUTPUT_DIR / "sprachnudel_complete_patch.jsonl"
PROGRESS_FILE = OUTPUT_DIR / "sn_complete_progress.json"
LOG_FILE = OUTPUT_DIR / "sn_complete_log.txt"
SEED_FILE = OUTPUT_DIR / "sn_complete_seed.txt"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "de-DE,de;q=0.9",
}

PROXY_FILE = BASE_DIR / "proxies.txt"
PROXY_API_URL = "https://proxy.webshare.io/api/v2/proxy/list/download/erowjqxnflvrgjscxtewuwuogwyttgrjtamztbwn/de/any/username/backbone/-/?plan_id=13419946"

UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 OPR/111.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]


class ProxyRotator:
    def __init__(self, proxy_file: Path = PROXY_FILE, api_url: str | None = None):
        self.proxies = []
        self.index = 0
        self.current = None
        self.stats = {"total": 0, "ok": 0, "blocked": 0, "failed": 0}
        self._lock = threading.Lock()
        self._is_rotating = False
        self._blocked_until: dict[str, float] = {}
        self._block_duration = 600
        url = api_url or PROXY_API_URL
        self._load_api(url) if url else self._load_file(proxy_file)

    def _load_api(self, url: str):
        print(f"ProxyRotator: Lade Rotating Residential DE-Liste...")
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            for line in r.text.strip().split("\n"):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(":")
                if len(parts) == 4:
                    ip, port, user, pwd = parts
                    proxy_url = f"http://{user}:{pwd}@{ip}:{port}"
                else:
                    continue
                self.proxies.append(proxy_url)
            if self.proxies:
                hosts = set(p.split("@")[-1].split(":")[0] for p in self.proxies)
                self._is_rotating = len(hosts) <= 3
                self.current = random.choice(self.proxies)
                mode = "ROTATING" if self._is_rotating else "STATIC"
                print(f"ProxyRotator: {len(self.proxies)} DE-Proxys ({mode}, {len(hosts)} Gateways)")
            else:
                print("ProxyRotator: Leere API-Antwort, fallback auf Datei")
                self._load_file(PROXY_FILE)
        except Exception as e:
            print(f"ProxyRotator: API-Fehler ({e}), fallback auf Datei")
            self._load_file(PROXY_FILE)

    def _load_file(self, path: Path):
        if not path.exists():
            print(f"Keine Proxy-Datei gefunden: {path}")
            return
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(":")
                if len(parts) == 4:
                    ip, port, user, pwd = parts
                    line = f"http://{user}:{pwd}@{ip}:{port}"
                elif "://" not in line:
                    line = "http://" + line
                self.proxies.append(line)
        random.shuffle(self.proxies)
        if self.proxies:
            self.current = self.proxies[0]
            print(f"ProxyRotator: {len(self.proxies)} Proxys aus Datei geladen")

    @property
    def active(self) -> bool:
        return len(self.proxies) > 0

    def get_proxy_dict(self) -> dict | None:
        if not self.current:
            return None
        return {"http": self.current, "https": self.current}

    def rotate(self):
        if not self.proxies:
            return
        self.index = (self.index + 1) % len(self.proxies)
        self.current = self.proxies[self.index]

    def on_ok(self):
        with self._lock:
            self.stats["total"] += 1
            self.stats["ok"] += 1

    def on_block(self):
        with self._lock:
            self.stats["total"] += 1
            self.stats["blocked"] += 1
            if self._is_rotating:
                self.current = random.choice(self.proxies)
            else:
                old = self.current
                self.rotate()
                if self.current != old:
                    print(f"Proxy rotiert: {old} -> {self.current}")

    def on_fail(self):
        with self._lock:
            self.stats["total"] += 1
            self.stats["failed"] += 1
            if self._is_rotating:
                self.current = random.choice(self.proxies)
            else:
                old = self.current
                self.rotate()

    def random_ua(self) -> str:
        return random.choice(UA_LIST)

    def get_available_proxy(self) -> tuple[str | None, dict | None]:
        with self._lock:
            if self._is_rotating:
                proxy = random.choice(self.proxies)
                self.current = proxy
                return proxy, {"http": proxy, "https": proxy}
            now = time.time()
            self._blocked_until = {k: v for k, v in self._blocked_until.items() if v > now}
            available = [p for p in self.proxies if p not in self._blocked_until]
            if not available:
                return None, None
            proxy = random.choice(available)
            self.current = proxy
            return proxy, {"http": proxy, "https": proxy}

    def block_proxy(self, proxy_url: str, duration: float | None = None):
        if self._is_rotating:
            return
        with self._lock:
            dur = duration or self._block_duration
            self._blocked_until[proxy_url] = time.time() + dur

    @property
    def blocked_count(self) -> int:
        if self._is_rotating:
            return 0
        now = time.time()
        return sum(1 for v in self._blocked_until.values() if v > now)

    @property
    def available_count(self) -> int:
        if self._is_rotating:
            return len(self.proxies)
        now = time.time()
        blocked = {k for k, v in self._blocked_until.items() if v > now}
        return sum(1 for p in self.proxies if p not in blocked)

SILBEN_MAP = {
    "Mit einer Silbe": 1, "Mit 2 Silben": 2, "Mit 3 Silben": 3,
    "Mit 4 Silben": 4, "Mit 5 Silben": 5, "Mit 6 Silben": 6,
    "Mit 7 Silben": 7, "Mit 8 Silben": 8, "Mit 9 Silben": 9,
}
RESULT_CLASSES = {"mb-2", "ms-2"}
ALLOWED_WORTARTEN = {"allgemein", "adjektive", "substantive", "verben"}
VOWELS = set("aeiouäöüy")

P0 = 0
P1 = 1
P2 = 2
P3 = 3
SNOWBALL_BATCH = 500
MAX_SNOWBALL_ROUNDS = 10


@dataclass
class Job:
    word: str
    priority: int = P3
    order: int = 0
    attempts: int = 0
    next_retry_ts: float = 0.0
    last_error: str = ""
    success_count: int = 0

    def sort_key(self) -> tuple:
        return (self.priority, self.next_retry_ts, self.order)

    def __lt__(self, other: "Job") -> bool:
        return self.sort_key() < other.sort_key()

    def to_dict(self) -> dict:
        return {
            "word": self.word, "priority": self.priority, "order": self.order,
            "attempts": self.attempts, "next_retry_ts": self.next_retry_ts,
            "last_error": self.last_error, "success_count": self.success_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        return cls(
            word=data["word"], priority=int(data.get("priority", P3)),
            order=int(data.get("order", 0)), attempts=int(data.get("attempts", 0)),
            next_retry_ts=float(data.get("next_retry_ts", 0.0)),
            last_error=data.get("last_error", ""),
            success_count=int(data.get("success_count", 0)),
        )


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
        elif tag == "li" and classes & RESULT_CLASSES:
            self._in_li_result = True
        elif tag == "a" and self._in_li_result:
            href = attr_dict.get("href", "")
            if "/woerterbuch/" in href:
                self._in_a = True
                self._a_text = ""

    def handle_endtag(self, tag):
        if tag == "h1":
            self._in_h1 = False
            match = re.search(r"(\d+)\s+Reimw", self._h1_buf)
            if match:
                self.count = int(match.group(1))
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
                self.results.append({"wort": wort, "wortart": self._wortart.lower(), "silben": silben})

    def handle_data(self, data):
        stripped = data.strip()
        if not stripped:
            return
        if self._in_h1:
            self._h1_buf += stripped
        if self._in_h4:
            self._h4_buf += stripped
        if self._in_h5:
            self._h5_buf += stripped
        if self._in_a:
            self._a_text += data


class AdaptiveThrottle:
    def __init__(self, base_delay: float, min_delay: float, max_delay: float):
        self.delay = base_delay
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.success_streak = 0
        self.recent = deque(maxlen=40)
        self.cooldown_until = 0.0

    def _error_rate(self) -> float:
        if not self.recent:
            return 0.0
        return sum(1 for x in self.recent if x != "ok") / len(self.recent)

    def on_success(self) -> str | None:
        self.recent.append("ok")
        self.success_streak += 1
        if self.success_streak >= 5 and self._error_rate() <= 0.15:
            old = self.delay
            if self.delay > self.min_delay * 4:
                self.delay = max(self.min_delay * 2, round(self.delay * 0.5, 3))
            else:
                self.delay = max(self.min_delay, round(self.delay * 0.85, 3))
            self.success_streak = 0
            if self.delay < old:
                return f"Tempo+: {old:.2f}s -> {self.delay:.2f}s"
        return None

    def on_error(self, kind: str) -> list[str]:
        self.recent.append(kind)
        self.success_streak = 0
        msgs = []
        old = self.delay
        factor = {"403": 1.8, "429": 2.0, "network": 1.35, "http": 1.03, "parse": 1.25}.get(kind, 1.2)
        self.delay = min(self.max_delay, round(self.delay * factor, 3))
        if self.delay > old:
            msgs.append(f"Tempo-: {old:.2f}s -> {self.delay:.2f}s ({kind})")
        counts = Counter(x for x in self.recent if x != "ok")
        now = time.time()
        load_errs = counts["403"] + counts["429"] + counts["network"]
        if kind == "403" and counts["403"] >= 5:
            wait = min(20 * 60, 60 * counts["403"])
            self.cooldown_until = max(self.cooldown_until, now + wait)
            msgs.append(f"403-Haeufung -> Cooldown {wait // 60}min")
        elif kind == "429" and counts["429"] >= 4:
            wait = min(15 * 60, 45 * counts["429"])
            self.cooldown_until = max(self.cooldown_until, now + wait)
            msgs.append(f"429-Haeufung -> Cooldown {wait // 60}min")
        elif load_errs >= 6 and len(self.recent) >= 12:
            wait = min(10 * 60, 30 * len(self.recent))
            self.cooldown_until = max(self.cooldown_until, now + wait)
            msgs.append(f"Load-Fehler ({load_errs}/{len(self.recent)}) -> Cooldown {wait}s")
        return msgs

    def to_dict(self) -> dict:
        return {"delay": self.delay, "min_delay": self.min_delay, "max_delay": self.max_delay,
                "success_streak": self.success_streak, "recent": list(self.recent),
                "cooldown_until": self.cooldown_until}

    @classmethod
    def from_dict(cls, d: dict, fb: float, fmin: float, fmax: float) -> "AdaptiveThrottle":
        t = cls(float(d.get("delay", fb)), float(d.get("min_delay", fmin)), float(d.get("max_delay", fmax)))
        t.success_streak = int(d.get("success_streak", 0))
        t.recent.extend(d.get("recent", []))
        t.cooldown_until = float(d.get("cooldown_until", 0.0))
        return t


def count_silben(wort: str) -> int:
    count = 0
    prev = False
    for ch in wort.lower():
        is_v = ch in VOWELS
        if is_v and not prev:
            count += 1
        prev = is_v
    return max(1, count)


def get_klang(wort: str) -> str:
    w = wort.lower().strip()
    for i in range(len(w) - 1, -1, -1):
        if w[i] in VOWELS:
            return w[i:]
    return w[-3:] if len(w) >= 3 else w


def log(msg: str, log_file: Path):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def html_is_challenge(html: str) -> bool:
    if len(html) < 2000:
        return True
    real_indicators = [
        "Reimw\u00f6rterbuch", "Suchtreffer", "list-unstyled",
        "sprachnudel.de", "Rhyme", "reimen",
    ]
    found_real = sum(1 for ind in real_indicators if ind in html)
    if found_real >= 1:
        return False
    challenge_indicators = [
        "Nur einen Moment",
        "Just a moment",
        "cf-browser-verification",
        "challenge-running",
        "window._cf_chl",
        "Checking your browser",
    ]
    return any(ind in html for ind in challenge_indicators)


def build_entry(word: str, parser: RhymeParser, blocked: bool = False) -> dict:
    silben = count_silben(word)
    sw_entry = {"wort": word, "wortart": "suchwort", "silben": silben}
    cnt = parser.count or len(parser.results)
    return {
        "suchwort": word, "klang": get_klang(word), "count": cnt,
        "suchwort_silben": silben,
        "results": [sw_entry] + parser.results,
        "source_url": f"{BASE_URL}?term={word}&type=rhyme&extended=1",
        "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "blocked": blocked,
    }


def fetch_word(session: requests.Session, word: str, proxy_rotator: "ProxyRotator | None" = None, proxy_url: str | None = None, cf_cookies: dict | None = None, cf_ua: str | None = None) -> tuple[str, dict | None, str]:
    headers = dict(HEADERS)
    proxies = None
    ext_proxy = proxy_url is not None
    if cf_ua:
        headers["User-Agent"] = cf_ua
    elif proxy_rotator and proxy_rotator.active:
        headers["User-Agent"] = proxy_rotator.random_ua()
        if proxy_url:
            proxies = {"http": proxy_url, "https": proxy_url}
        else:
            proxies = proxy_rotator.get_proxy_dict()
    cookies = cf_cookies or None
    try:
        r = session.get(BASE_URL, params={"term": word, "type": "rhyme", "extended": "1"},
                        timeout=20, headers=headers, proxies=proxies, cookies=cookies)
    except requests.exceptions.RequestException as exc:
        if proxy_rotator and not ext_proxy:
            proxy_rotator.on_fail()
        return "network", None, str(exc)
    if r.status_code == 200:
        if proxy_rotator and not ext_proxy:
            proxy_rotator.on_ok()
        if html_is_challenge(r.text):
            debug_path = OUTPUT_DIR / "_debug_blocked_response.html"
            with open(debug_path, "w", encoding="utf-8") as df:
                df.write(f"<!-- word={word} len={len(r.text)} url={r.url} -->\n")
                df.write(r.text[:5000])
            return "blocked", build_entry(word, RhymeParser(), blocked=True), "CF-Challenge erkannt"
        p = RhymeParser()
        p.feed(r.text)
        return "ok", build_entry(word, p), f"{p.count or len(p.results)} Treffer"
    if r.status_code == 404:
        if proxy_rotator and not ext_proxy:
            proxy_rotator.on_ok()
        return "ok", build_entry(word, RhymeParser()), "404 -> leer"
    if r.status_code == 403:
        if proxy_rotator and not ext_proxy:
            proxy_rotator.on_block()
        return "403", None, "HTTP 403"
    if r.status_code == 429:
        if proxy_rotator and not ext_proxy:
            proxy_rotator.on_block()
        return "429", None, "HTTP 429"
    if r.status_code == 500:
        if proxy_rotator and not ext_proxy:
            proxy_rotator.on_block()
        return "http", None, "HTTP 500"
    return "http", None, f"HTTP {r.status_code}"


def compute_retry_delay(job: Job, kind: str, throttle: AdaptiveThrottle) -> float:
    base = {"403": 60.0, "429": 75.0, "network": 30.0, "http": 20.0}.get(kind, 20.0)
    exponent = min(job.attempts, 5)
    jitter = random.uniform(0.85, 1.2)
    delay = base * (2 ** exponent) * jitter
    delay = max(delay, throttle.delay * 2.0)
    return min(delay, 3600.0)


def load_snapshot_words(path: Path) -> set[str]:
    words = set()
    if not path.exists():
        return words
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                w = entry.get("suchwort", "")
                if w:
                    words.add(w.casefold())
            except json.JSONDecodeError:
                continue
    return words


def load_patch_entries(path: Path) -> dict[str, dict]:
    entries = {}
    if not path.exists():
        return entries
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            word = entry.get("suchwort")
            if word:
                entries[word] = entry
    return entries


def write_patch_entries(path: Path, entries: dict[str, dict]):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as out:
        for sw in sorted(entries, key=str.casefold):
            out.write(json.dumps(entries[sw], ensure_ascii=False) + "\n")
    tmp.replace(path)


def choose_better_entry(old: dict | None, new: dict) -> dict:
    if old is None:
        return new
    old_s = (int(old.get("count", 0)), len(old.get("results", [])))
    new_s = (int(new.get("count", 0)), len(new.get("results", [])))
    return new if new_s >= old_s else old


def load_sitemap_words() -> list[str]:
    log(f"Lade Sitemap von sprachnudel.de ...", LOG_FILE)
    r = requests.get(SITEMAP_URL, headers=HEADERS, timeout=30)
    r.raise_for_status()
    raw_xml = gzip.decompress(r.content).decode("utf-8")
    urls = re.findall(r'<loc>https://www\.sprachnudel\.de/woerterbuch/(.*?)</loc>', raw_xml)
    words = []
    seen = set()
    for u in urls:
        decoded = unquote(u).strip()
        if "-" in decoded:
            continue
        decoded = re.sub(r'_\d+$', '', decoded)
        key = decoded.casefold()
        if key not in seen:
            seen.add(key)
            words.append(decoded)
    log(f"Sitemap: {len(words)} Woerter", LOG_FILE)
    return words


def load_derewo_words() -> list[tuple[str, int]]:
    log(f"Lade DeReWo-Liste ...", LOG_FILE)
    r = requests.get(DEREWO_URL, timeout=60)
    r.raise_for_status()
    data = None
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        for name in z.namelist():
            if name.endswith(".txt"):
                data = z.read(name).decode("latin-1", errors="replace")
                break
    if not data:
        log("FEHLER: Keine txt-Datei im DeReWo-ZIP!", LOG_FILE)
        return []
    lines = [l for l in data.strip().split("\n") if not l.startswith("#")]
    result = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if not parts:
            continue
        word = parts[0]
        freq_class = int(parts[1]) if len(parts) > 1 else 99
        if not re.match(r'^[a-zA-ZäöüÄÖÜß]+$', word):
            continue
        if ',' in word or '(' in word or ')' in word:
            continue
        result.append((word, freq_class))
    log(f"DeReWo: {len(result)} gueltige Woerter", LOG_FILE)
    return result


def load_thema_words() -> list[str]:
    if not THEMA_FILE.exists():
        log(f"Themadatei nicht gefunden: {THEMA_FILE}", LOG_FILE)
        return []
    with open(THEMA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    words = data.get("woerter", [])
    log(f"Thematische Ergaenzung: {len(words)} Woerter", LOG_FILE)
    return words


def extract_rhyme_words(entry: dict) -> set[str]:
    words = set()
    for r in entry.get("results", []):
        w = r.get("wort", "")
        if w and r.get("wortart") != "suchwort":
            words.add(w)
    return words


def build_seed_queue(
    snapshot_words: set[str],
    completed: set[str],
    deferred: dict[str, dict],
    existing_jobs: dict[str, Job],
) -> dict[str, Job]:
    jobs = dict(existing_jobs)
    order = len(jobs)

    thema_words = load_thema_words()
    for w in thema_words:
        key = w.casefold()
        if key in snapshot_words or key in completed or key in deferred or key in jobs:
            continue
        jobs[key] = Job(word=w, priority=P0, order=order)
        order += 1

    derewo_raw = load_derewo_words()
    derewo_p1 = [(w, fc) for w, fc in derewo_raw if fc <= 8]
    derewo_p2 = [(w, fc) for w, fc in derewo_raw if fc > 8]
    for w, _ in derewo_p1:
        key = w.casefold()
        if key in snapshot_words or key in completed or key in deferred or key in jobs:
            continue
        jobs[key] = Job(word=w, priority=P1, order=order)
        order += 1
    for w, _ in derewo_p2:
        key = w.casefold()
        if key in snapshot_words or key in completed or key in deferred or key in jobs:
            continue
        jobs[key] = Job(word=w, priority=P2, order=order)
        order += 1

    sitemap_words = load_sitemap_words()
    for w in sitemap_words:
        key = w.casefold()
        if key in snapshot_words or key in completed or key in deferred or key in jobs:
            continue
        jobs[key] = Job(word=w, priority=P2, order=order)
        order += 1

    return jobs


def add_snowball_words(
    new_entries: list[dict],
    snapshot_words: set[str],
    completed: set[str],
    deferred: dict[str, dict],
    jobs: dict[str, Job],
    patch_entries: dict[str, dict],
) -> int:
    discovered = set()
    for entry in new_entries:
        for w in extract_rhyme_words(entry):
            discovered.add(w)
    added = 0
    for w in discovered:
        key = w.casefold()
        if key in snapshot_words or key in completed or key in deferred or key in jobs:
            continue
        if any(key == pe.casefold() for pe in patch_entries):
            continue
        jobs[key] = Job(word=w, priority=P3, order=len(jobs))
        added += 1
    return added


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="3-Stufen Sprachnudel-Complete-Scrape")
    p.add_argument("--base-delay", type=float, default=1.0)
    p.add_argument("--min-delay", type=float, default=0.6)
    p.add_argument("--max-delay", type=float, default=12.0)
    p.add_argument("--max-attempts", type=int, default=8)
    p.add_argument("--status-every", type=int, default=50)
    p.add_argument("--save-every", type=int, default=10)
    p.add_argument("--snowball-rounds", type=int, default=MAX_SNOWBALL_ROUNDS)
    p.add_argument("--reset", action="store_true")
    p.add_argument("--no-snowball", action="store_true")
    p.add_argument("--seed-only", action="store_true", help="Nur Seed aufbauen und speichern")
    return p.parse_args()


class BFSFrontier:
    """Persistierbare BFS-Frontier fuer Schneeball-Wortentdeckung."""

    def __init__(self, path: Path):
        self.path = path
        self._queue: deque[str] = deque()
        self._seen: set[str] = set()
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for w in data.get("queue", []):
                key = w.casefold()
                if key not in self._seen:
                    self._queue.append(w)
                    self._seen.add(key)
            for w in data.get("done", []):
                self._seen.add(w.casefold())
        except (json.JSONDecodeError, OSError):
            pass

    def save(self):
        with self._lock:
            payload = {
                "queue": list(self._queue),
                "done": [w for w in self._seen if w not in {q.casefold() for q in self._queue}],
                "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        tmp.replace(self.path)

    def push(self, word: str) -> bool:
        key = word.casefold()
        with self._lock:
            if key in self._seen:
                return False
            self._queue.append(word)
            self._seen.add(key)
            return True

    def push_many(self, words: Iterable[str]) -> int:
        added = 0
        for w in words:
            if self.push(w):
                added += 1
        return added

    def pop(self) -> str | None:
        with self._lock:
            if not self._queue:
                return None
            return self._queue.popleft()

    def peek(self) -> str | None:
        with self._lock:
            return self._queue[0] if self._queue else None

    def mark_done(self, word: str):
        key = word.casefold()
        with self._lock:
            self._seen.add(key)

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._queue)

    @property
    def total_seen(self) -> int:
        with self._lock:
            return len(self._seen)

    def __len__(self) -> int:
        return self.size

    def __bool__(self) -> bool:
        return self.size > 0


def main():
    args = parse_args()

    log("=== 3-Stufen Sprachnudel-Complete-Scrape ===", LOG_FILE)

    snapshot_words = load_snapshot_words(SNAPSHOT_V2)
    log(f"Snapshot v2: {len(snapshot_words)} Woerter bereits vorhanden", LOG_FILE)

    progress = {}
    if not args.reset and PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            progress = json.load(f)
        log(f"Progress geladen: {len(progress.get('completed', []))} erledigt", LOG_FILE)
    elif args.reset and PROGRESS_FILE.exists():
        PROGRESS_FILE.unlink()
        log("Progress zurueckgesetzt", LOG_FILE)

    completed = set(progress.get("completed", []))
    deferred = dict(progress.get("deferred", {}))
    existing_jobs = {}
    for item in progress.get("jobs", []):
        job = Job.from_dict(item)
        if job.word not in completed and job.word not in deferred:
            existing_jobs[job.word.casefold()] = job

    if args.seed_only or not existing_jobs:
        if args.seed_only:
            log("Seed-only Modus: Baue Seed-Queue auf ...", LOG_FILE)
        else:
            log("Keine Jobs im Progress -> Baue Seed-Queue auf ...", LOG_FILE)
        jobs = build_seed_queue(snapshot_words, completed, deferred, existing_jobs)
        log(f"Seed-Queue: {len(jobs)} Jobs", LOG_FILE)
        p0_count = sum(1 for j in jobs.values() if j.priority == P0)
        p1_count = sum(1 for j in jobs.values() if j.priority == P1)
        p2_count = sum(1 for j in jobs.values() if j.priority == P2)
        p3_count = sum(1 for j in jobs.values() if j.priority == P3)
        log(f"  P0 (Thema): {p0_count} | P1 (DeReWo top): {p1_count} | P2 (DeReWo rest): {p2_count} | P3 (Sitemap): {p3_count}", LOG_FILE)
        if args.seed_only:
            with open(SEED_FILE, "w", encoding="utf-8") as f:
                for j in sorted(jobs.values(), key=lambda x: (x.priority, x.order)):
                    f.write(f"{j.priority}\t{j.word}\n")
            log(f"Seed gespeichert in {SEED_FILE}", LOG_FILE)
            return
    else:
        jobs = existing_jobs
        log(f"Bestehende Jobs fortgesetzt: {len(jobs)} offen", LOG_FILE)

    patch_entries = load_patch_entries(PATCH_FILE)
    log(f"Patch: {len(patch_entries)} Eintraege bereits vorhanden", LOG_FILE)

    if "throttle" in progress:
        throttle = AdaptiveThrottle.from_dict(
            progress["throttle"], args.base_delay, args.min_delay, args.max_delay
        )
    else:
        throttle = AdaptiveThrottle(args.base_delay, args.min_delay, args.max_delay)

    stats = progress.get("stats", {
        "requests": 0, "success": 0, "empty": 0, "deferred": 0,
        "errors": {"403": 0, "429": 0, "network": 0, "http": 0},
        "snowball_added": 0, "snowball_rounds_done": 0,
        "started_at": time.time(),
    })

    log(f"Starte Scraping: {len(jobs)} Jobs | Delay: {throttle.delay:.2f}s", LOG_FILE)

    # ProxyRotator initialisieren
    proxy_rotator = ProxyRotator()
    if proxy_rotator.active:
        log(f"ProxyRotator aktiv: {proxy_rotator.available_count} Proxys verfuegbar", LOG_FILE)
    else:
        log("Keine Proxys konfiguriert – direkte Requests", LOG_FILE)

    # CF-Solver Hintergrund-Thread starten
    cookies = get_cf_cookies()
    if cookies:
        log(f"CF-Cookie aktiv ({len(cookies)} Cookies), starte Background-Solver", LOG_FILE)
        start_background_solver(interval=2400)
    else:
        log("Kein CF-Cookie vorhanden — starte ohne (evtl. blockiert)", LOG_FILE)

    session = requests.Session()
    session.headers.update(HEADERS)
    next_request_ts = time.time()
    processed_since_save = 0
    batch_new_entries = []
    total_snowball_rounds = int(stats.get("snowball_rounds_done", 0))

    # Heap fuer O(log n) Job-Auswahl
    job_heap: list[Job] = sorted(jobs.values(), key=lambda j: j.sort_key())
    heapq.heapify(job_heap)
    jobs_dirty = False

    while job_heap or jobs_dirty:
        if jobs_dirty:
            job_heap = list(jobs.values())
            heapq.heapify(job_heap)
            jobs_dirty = False

        if not job_heap:
            break

        now = time.time()
        if throttle.cooldown_until > now:
            time.sleep(min(throttle.cooldown_until - now, 10.0))
            continue

        job = heapq.heappop(job_heap)

        # Skip wenn Job zwischenzeitlich aus jobs dict entfernt wurde
        if job.word.casefold() not in jobs:
            continue

        wait_until = max(next_request_ts, job.next_retry_ts)
        now = time.time()
        if wait_until > now:
            # Zurueck in den Heap, warte kurz
            heapq.heappush(job_heap, job)
            time.sleep(min(wait_until - now, 5.0))
            continue

        cf_cookies = get_cf_cookies()
        cf_ua = get_ua()
        outcome, entry, message = fetch_word(
            session, job.word,
            proxy_rotator=proxy_rotator if proxy_rotator.active else None,
            cf_cookies=cf_cookies or None,
            cf_ua=cf_ua if cf_cookies else None,
        )
        stats["requests"] += 1
        processed_since_save += 1

        if outcome == "ok":
            job.success_count += 1
            patch_entries[job.word] = choose_better_entry(patch_entries.get(job.word), entry)
            completed.add(job.word.casefold())
            del jobs[job.word.casefold()]

            if int(entry.get("count", 0)) > 0:
                stats["success"] += 1
                batch_new_entries.append(entry)
            else:
                stats["empty"] += 1

            speed_msg = throttle.on_success()
            if speed_msg:
                log(speed_msg, LOG_FILE)
        else:
            stats["errors"][outcome] = stats["errors"].get(outcome, 0) + 1
            job.attempts += 1
            job.last_error = message

            if job.attempts >= args.max_attempts:
                deferred[job.word] = {
                    "attempts": job.attempts, "last_error": message,
                    "priority": job.priority,
                    "deferred_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                del jobs[job.word.casefold()]
                stats["deferred"] += 1
            else:
                retry_in = compute_retry_delay(job, outcome, throttle)
                job.next_retry_ts = time.time() + retry_in
                heapq.heappush(job_heap, job)

            for tm in throttle.on_error(outcome):
                log(tm, LOG_FILE)

        next_request_ts = time.time() + throttle.delay

        if (stats["requests"] % args.status_every) == 0:
            elapsed = time.time() - float(stats["started_at"])
            rate = stats["requests"] / elapsed if elapsed > 0 else 0
            remaining = len(jobs)
            log(
                f"Status | req={stats['requests']} ok={stats['success']} leer={stats['empty']} "
                f"deferred={stats['deferred']} offen={remaining} rate={rate:.2f}/s "
                f"delay={throttle.delay:.2f}s patch={len(patch_entries)}",
                LOG_FILE,
            )

        if processed_since_save >= args.save_every:
            write_patch_entries(PATCH_FILE, patch_entries)
            progress_payload = {
                "completed": sorted(completed),
                "deferred": deferred,
                "jobs": [j.to_dict() for j in jobs.values()],
                "stats": stats,
                "throttle": throttle.to_dict(),
                "updated_at": time.time(),
            }
            with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
                json.dump(progress_payload, f, ensure_ascii=False, indent=2)
            processed_since_save = 0

        if not args.no_snowball and len(batch_new_entries) >= SNOWBALL_BATCH:
            total_snowball_rounds += 1
            if total_snowball_rounds <= args.snowball_rounds:
                added = add_snowball_words(
                    batch_new_entries, snapshot_words, completed, deferred, jobs, patch_entries
                )
                stats["snowball_added"] = int(stats.get("snowball_added", 0)) + added
                stats["snowball_rounds_done"] = total_snowball_rounds
                log(f"Schneeball Runde {total_snowball_rounds}: {added} neue Woerter entdeckt -> Queue: {len(jobs)}", LOG_FILE)
                if added > 0:
                    jobs_dirty = True
            batch_new_entries = []

    if batch_new_entries and not args.no_snowball:
        total_snowball_rounds += 1
        if total_snowball_rounds <= args.snowball_rounds:
            added = add_snowball_words(
                batch_new_entries, snapshot_words, completed, deferred, jobs, patch_entries
            )
            stats["snowball_added"] = int(stats.get("snowball_added", 0)) + added
            stats["snowball_rounds_done"] = total_snowball_rounds
            if added > 0:
                log(f"Schneeball Final-Runde: {added} neue Woerter", LOG_FILE)
            batch_new_entries = []

    write_patch_entries(PATCH_FILE, patch_entries)
    progress_payload = {
        "completed": sorted(completed),
        "deferred": deferred,
        "jobs": [j.to_dict() for j in sorted(jobs.values(), key=lambda x: (x.priority, x.next_retry_ts))],
        "stats": stats,
        "throttle": throttle.to_dict(),
        "updated_at": time.time(),
    }
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress_payload, f, ensure_ascii=False, indent=2)

    log(
        f"FERTIG | erledigt={len(completed)} deferred={len(deferred)} "
        f"mit Reimen={stats['success']} leer={stats['empty']} "
        f"schneeball={stats.get('snowball_added', 0)} patch={len(patch_entries)}",
        LOG_FILE,
    )


if __name__ == "__main__":
    main()
