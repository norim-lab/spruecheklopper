import argparse
import json
import random
import re
import time
from collections import Counter, deque
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

import requests


BASE_DIR = Path(r"C:\Users\miron\Documents\trae_projects\scrape")
OUTPUT_DIR = BASE_DIR / "output"

BASE_URL = "https://www.sprachnudel.de/search"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "de-DE,de;q=0.9",
}

RAW_SNAPSHOT_F = OUTPUT_DIR / "sprachnudel_raw.snapshot.jsonl"
PATCH_F = OUTPUT_DIR / "sprachnudel_targeted_patch.jsonl"
PROGRESS_F = OUTPUT_DIR / "sn_targeted_progress.json"
LOG_F = OUTPUT_DIR / "sn_targeted_log.txt"

SILBEN_MAP = {
    "Mit einer Silbe": 1,
    "Mit 2 Silben": 2,
    "Mit 3 Silben": 3,
    "Mit 4 Silben": 4,
    "Mit 5 Silben": 5,
    "Mit 6 Silben": 6,
    "Mit 7 Silben": 7,
    "Mit 8 Silben": 8,
    "Mit 9 Silben": 9,
}
RESULT_CLASSES = {"mb-2", "ms-2"}
ALLOWED_WORTARTEN = {"allgemein", "adjektive", "substantive", "verben"}
VOWELS = set("aeiouäöüy")


@dataclass
class Job:
    word: str
    order: int = 0
    attempts: int = 0
    next_retry_ts: float = 0.0
    last_error: str = ""
    success_count: int = 0

    def to_dict(self) -> dict:
        return {
            "word": self.word,
            "order": self.order,
            "attempts": self.attempts,
            "next_retry_ts": self.next_retry_ts,
            "last_error": self.last_error,
            "success_count": self.success_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        return cls(
            word=data["word"],
            order=int(data.get("order", 0)),
            attempts=int(data.get("attempts", 0)),
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
        elif tag == "li" and RESULT_CLASSES.issubset(classes):
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
                self.results.append(
                    {
                        "wort": wort,
                        "wortart": self._wortart.lower(),
                        "silben": silben,
                    }
                )

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
        errors = sum(1 for item in self.recent if item != "ok")
        return errors / len(self.recent)

    def on_success(self) -> str | None:
        self.recent.append("ok")
        self.success_streak += 1
        msg = None
        if self.success_streak >= 8 and self._error_rate() <= 0.15:
            old_delay = self.delay
            self.delay = max(self.min_delay, round(self.delay * 0.9, 3))
            self.success_streak = 0
            if self.delay < old_delay:
                msg = f"Tempo leicht erhoeht: Delay {old_delay:.2f}s -> {self.delay:.2f}s"
        return msg

    def on_error(self, kind: str) -> list[str]:
        self.recent.append(kind)
        self.success_streak = 0
        messages = []

        old_delay = self.delay
        factor = {
            "403": 1.8,
            "429": 2.0,
            "network": 1.35,
            "http": 1.03,
            "parse": 1.25,
        }.get(kind, 1.2)
        self.delay = min(self.max_delay, round(self.delay * factor, 3))
        if self.delay > old_delay:
            messages.append(f"Tempo reduziert: Delay {old_delay:.2f}s -> {self.delay:.2f}s ({kind})")

        counts = Counter(x for x in self.recent if x != "ok")
        now = time.time()
        load_related_errors = counts["403"] + counts["429"] + counts["network"]

        if kind == "403" and counts["403"] >= 5:
            wait = min(20 * 60, 60 * counts["403"])
            self.cooldown_until = max(self.cooldown_until, now + wait)
            messages.append(f"403-Haeufung erkannt -> globaler Cooldown {wait // 60}min")
        elif kind == "429" and counts["429"] >= 4:
            wait = min(15 * 60, 45 * counts["429"])
            self.cooldown_until = max(self.cooldown_until, now + wait)
            messages.append(f"429-Haeufung erkannt -> globaler Cooldown {wait // 60}min")
        elif load_related_errors >= 6 and len(self.recent) >= 12:
            wait = min(10 * 60, 30 * len(self.recent))
            self.cooldown_until = max(self.cooldown_until, now + wait)
            messages.append(f"Load-Fehler haeufig ({load_related_errors}/{len(self.recent)}) -> Cooldown {wait}s")

        return messages

    def to_dict(self) -> dict:
        return {
            "delay": self.delay,
            "min_delay": self.min_delay,
            "max_delay": self.max_delay,
            "success_streak": self.success_streak,
            "recent": list(self.recent),
            "cooldown_until": self.cooldown_until,
        }

    @classmethod
    def from_dict(cls, data: dict, fallback_base: float, fallback_min: float, fallback_max: float) -> "AdaptiveThrottle":
        throttle = cls(
            base_delay=float(data.get("delay", fallback_base)),
            min_delay=float(data.get("min_delay", fallback_min)),
            max_delay=float(data.get("max_delay", fallback_max)),
        )
        throttle.success_streak = int(data.get("success_streak", 0))
        throttle.recent.extend(data.get("recent", []))
        throttle.cooldown_until = float(data.get("cooldown_until", 0.0))
        return throttle


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
    timestamp = time.strftime("%H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def parse_words_arg(text: str) -> list[str]:
    words = []
    for part in text.split(","):
        value = part.strip()
        if value:
            words.append(value)
    return words


def load_words_file(path: Path) -> list[str]:
    words = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            value = line.strip()
            if value and not value.startswith("#"):
                words.append(value)
    return words


def dedupe_words(words: list[str]) -> list[str]:
    seen = set()
    out = []
    for word in words:
        key = word.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(word)
    return out


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
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as out:
        for suchwort in sorted(entries, key=str.casefold):
            out.write(json.dumps(entries[suchwort], ensure_ascii=False) + "\n")
    tmp_path.replace(path)


def load_progress(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_progress(
    path: Path,
    targets: list[str],
    completed: set[str],
    deferred: dict[str, dict],
    jobs: dict[str, Job],
    stats: dict,
    throttle: AdaptiveThrottle,
):
    payload = {
        "targets": targets,
        "completed": sorted(completed),
        "deferred": deferred,
        "jobs": [job.to_dict() for job in sorted(jobs.values(), key=lambda item: (item.next_retry_ts, item.word.casefold()))],
        "stats": stats,
        "throttle": throttle.to_dict(),
        "updated_at": time.time(),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def choose_better_entry(old: dict | None, new: dict) -> dict:
    if old is None:
        return new
    old_score = (int(old.get("count", 0)), len(old.get("results", [])))
    new_score = (int(new.get("count", 0)), len(new.get("results", [])))
    return new if new_score >= old_score else old


def build_entry(word: str, parser: RhymeParser) -> dict:
    suchwort_silben = count_silben(word)
    suchwort_entry = {
        "wort": word,
        "wortart": "suchwort",
        "silben": suchwort_silben,
    }
    count = parser.count or len(parser.results)
    return {
        "suchwort": word,
        "klang": get_klang(word),
        "count": count,
        "suchwort_silben": suchwort_silben,
        "results": [suchwort_entry] + parser.results,
        "source_url": f"{BASE_URL}?term={word}&type=rhyme&extended=1",
        "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def fetch_word(session: requests.Session, word: str) -> tuple[str, dict | None, str]:
    try:
        response = session.get(
            BASE_URL,
            params={"term": word, "type": "rhyme", "extended": "1"},
            timeout=20,
        )
    except requests.exceptions.RequestException as exc:
        return "network", None, str(exc)

    if response.status_code == 200:
        parser = RhymeParser()
        parser.feed(response.text)
        return "ok", build_entry(word, parser), f"{parser.count or len(parser.results)} Treffer"

    if response.status_code == 404:
        return "ok", build_entry(word, RhymeParser()), "404 -> als leerer Treffer gespeichert"
    if response.status_code == 403:
        return "403", None, "HTTP 403"
    if response.status_code == 429:
        return "429", None, "HTTP 429"
    return "http", None, f"HTTP {response.status_code}"


def compute_retry_delay(job: Job, kind: str, throttle: AdaptiveThrottle) -> float:
    base = {
        "403": 60.0,
        "429": 75.0,
        "network": 30.0,
        "http": 20.0,
        "parse": 20.0,
    }.get(kind, 20.0)
    exponent = min(job.attempts, 5)
    jitter = random.uniform(0.85, 1.2)
    delay = base * (2 ** exponent) * jitter
    delay = max(delay, throttle.delay * 2.0)
    return min(delay, 60.0 * 60.0)


def build_jobs(targets: list[str], progress: dict) -> tuple[set[str], dict[str, dict], dict[str, Job]]:
    completed = set(progress.get("completed", []))
    deferred = dict(progress.get("deferred", {}))
    jobs = {}

    for item in progress.get("jobs", []):
        job = Job.from_dict(item)
        if job.word not in completed and job.word not in deferred:
            jobs[job.word] = job

    for order, word in enumerate(targets):
        if word in completed or word in deferred or word in jobs:
            continue
        jobs[word] = Job(word=word, order=order, next_retry_ts=0.0)

    return completed, deferred, jobs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gezieltes, adaptives Sprachnudel-Nachscraping.")
    parser.add_argument("--words", help="Komma-separierte Wortliste, z. B. magd,haus,raum")
    parser.add_argument("--words-file", help="Datei mit einem Wort pro Zeile")
    parser.add_argument("--patch-file", default=str(PATCH_F))
    parser.add_argument("--progress-file", default=str(PROGRESS_F))
    parser.add_argument("--log-file", default=str(LOG_F))
    parser.add_argument("--status-every", type=int, default=25)
    parser.add_argument("--base-delay", type=float, default=1.2)
    parser.add_argument("--min-delay", type=float, default=0.7)
    parser.add_argument("--max-delay", type=float, default=12.0)
    parser.add_argument("--max-attempts-per-word", type=int, default=12)
    parser.add_argument("--save-every", type=int, default=5)
    parser.add_argument("--reset", action="store_true", help="Ignoriert vorhandenen Progress fuer diesen Lauf")
    return parser.parse_args()


def main():
    args = parse_args()
    OUTPUT_DIR.mkdir(exist_ok=True)

    words = []
    if args.words:
        words.extend(parse_words_arg(args.words))
    if args.words_file:
        words.extend(load_words_file(Path(args.words_file)))
    words = dedupe_words(words)
    if not words:
        raise SystemExit("Bitte --words oder --words-file angeben.")

    patch_file = Path(args.patch_file)
    progress_file = Path(args.progress_file)
    log_file = Path(args.log_file)

    if args.reset and progress_file.exists():
        progress_file.unlink()
        log("Vorhandener Progress bewusst zurueckgesetzt.", log_file)

    progress = load_progress(progress_file)
    completed, deferred, jobs = build_jobs(words, progress)
    patch_entries = load_patch_entries(patch_file)

    if "throttle" in progress:
        throttle = AdaptiveThrottle.from_dict(
            progress["throttle"],
            fallback_base=args.base_delay,
            fallback_min=args.min_delay,
            fallback_max=args.max_delay,
        )
    else:
        throttle = AdaptiveThrottle(args.base_delay, args.min_delay, args.max_delay)

    stats = progress.get(
        "stats",
        {
            "requests": 0,
            "success": 0,
            "empty": 0,
            "deferred": 0,
            "errors": {"403": 0, "429": 0, "network": 0, "http": 0, "parse": 0},
            "started_at": time.time(),
        },
    )

    log("=== Intelligentes Sprachnudel-Nachscraping ===", log_file)
    log(f"Ziele gesamt: {len(words)} | erledigt: {len(completed)} | offen: {len(jobs)} | deferred: {len(deferred)}", log_file)
    log(f"Delay-Start: {throttle.delay:.2f}s | Fenster min/max: {throttle.min_delay:.2f}/{throttle.max_delay:.2f}s", log_file)
    log(f"Patch-Datei: {patch_file.name} | Progress: {progress_file.name}", log_file)

    session = requests.Session()
    session.headers.update(HEADERS)
    next_request_ts = time.time()
    processed_since_save = 0

    while jobs:
        now = time.time()
        if throttle.cooldown_until > now:
            sleep_for = min(throttle.cooldown_until - now, 10.0)
            time.sleep(max(0.5, sleep_for))
            continue

        job = min(jobs.values(), key=lambda item: (item.next_retry_ts, item.order, item.word.casefold()))
        wait_until = max(next_request_ts, job.next_retry_ts)
        now = time.time()
        if wait_until > now:
            time.sleep(min(wait_until - now, 5.0))
            continue

        outcome, entry, message = fetch_word(session, job.word)
        stats["requests"] += 1
        processed_since_save += 1

        if outcome == "ok":
            job.success_count += 1
            patch_entries[job.word] = choose_better_entry(patch_entries.get(job.word), entry)
            write_patch_entries(patch_file, patch_entries)
            completed.add(job.word)
            del jobs[job.word]

            if int(entry.get("count", 0)) > 0:
                stats["success"] += 1
                log(f"{job.word!r}: {entry['count']} Reime gespeichert", log_file)
            else:
                stats["empty"] += 1
                log(f"{job.word!r}: keine Reime gefunden, aber sauber gespeichert", log_file)

            speed_msg = throttle.on_success()
            if speed_msg:
                log(speed_msg, log_file)

        else:
            stats["errors"][outcome] = int(stats["errors"].get(outcome, 0)) + 1
            job.attempts += 1
            job.last_error = message

            if job.attempts >= args.max_attempts_per_word:
                deferred[job.word] = {
                    "attempts": job.attempts,
                    "last_error": message,
                    "deferred_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                del jobs[job.word]
                stats["deferred"] += 1
                log(f"{job.word!r}: nach {job.attempts} Versuchen auf deferred gesetzt ({message})", log_file)
            else:
                retry_in = compute_retry_delay(job, outcome, throttle)
                job.next_retry_ts = time.time() + retry_in
                log(f"{job.word!r}: {message} -> Retry in {retry_in:.0f}s (Versuch {job.attempts}/{args.max_attempts_per_word})", log_file)

            for throttle_msg in throttle.on_error(outcome):
                log(throttle_msg, log_file)

        next_request_ts = time.time() + throttle.delay

        if (stats["requests"] % args.status_every) == 0:
            elapsed = time.time() - float(stats["started_at"])
            rate = stats["requests"] / elapsed if elapsed > 0 else 0.0
            log(
                f"Status | req={stats['requests']} ok={stats['success']} leer={stats['empty']} "
                f"deferred={stats['deferred']} offen={len(jobs)} rate={rate:.2f}/s delay={throttle.delay:.2f}s",
                log_file,
            )

        if processed_since_save >= args.save_every:
            save_progress(progress_file, words, completed, deferred, jobs, stats, throttle)
            processed_since_save = 0

    save_progress(progress_file, words, completed, deferred, jobs, stats, throttle)
    write_patch_entries(patch_file, patch_entries)
    log(
        f"Fertig | erledigt={len(completed)} deferred={len(deferred)} "
        f"mit Reimen={stats['success']} leer={stats['empty']}",
        log_file,
    )
    log("Bestehender Snapshot wurde nicht ueberschrieben.", log_file)


if __name__ == "__main__":
    main()
