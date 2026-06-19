import json
import re
import sqlite3
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from spruch_app import generator as gen

OUT_DIR = ROOT / "output"

KURATIERTE_GRUPPEN = OUT_DIR / "reimgruppen_derb.jsonl"
CORPUS_DB = OUT_DIR / "sprueche.db"
FREQ_CACHE = OUT_DIR / "derewo_freq_cache.json"
REIMDB = OUT_DIR / "reimdb.sqlite"

REIMPOOL_OUT = OUT_DIR / "reimpool_R4.json"
SWEEP_OUT = OUT_DIR / "reimpool_R4_sweep.md"
FREQ_FULL_OUT = OUT_DIR / "freq_full.json"

TARGET_MIN_PARTNER = 12
THRESHOLDS = (8, 12, 15, 18)
MAX_T = max(THRESHOLDS)

_TRAIL_PUNCT_RE = re.compile(r"^[\(\[\{\"'“”‘’]+|[\)\]\}\"'“”‘’,.!?;:]+$")
_WORD_OK_RE = re.compile(r"^[A-Za-zÄÖÜäöüß]+$")
_FREMD_ENDINGS = (
    "tion", "sion", "ment", "ance", "ence", "ität",
    "ismus", "ieren", "abel", "ibel", "thek", "pell",
)
_SANITY_WORDS = ("witz", "blitz", "sitz", "spitze", "katze", "platz")
_KNAPP_ABER_BEKANNT = {"arsch", "umpf", "opf", "eck"}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _clean_word(word: str) -> str:
    w = unicodedata.normalize("NFKC", str(word)).strip()
    w = w.replace("\u00ad", "")
    w = _TRAIL_PUNCT_RE.sub("", w)
    return w


def _freq_key(word: str) -> str:
    return _clean_word(word).lower()


def _split_lines(text: str) -> list[str]:
    if text is None:
        return []
    return [z.strip() for z in str(text).replace("\\n", "\n").splitlines() if z.strip()]


def _looks_alltag(word: str) -> bool:
    w = _clean_word(word)
    if not w or not _WORD_OK_RE.match(w):
        return False
    wl = w.lower()
    if len(wl) > 22:
        return False
    if any(wl.endswith(e) for e in _FREMD_ENDINGS):
        return False
    return True


def _endung(word: str) -> str:
    w = _clean_word(word)
    if not w:
        return ""
    try:
        return gen._reim_endung(w)
    except Exception:
        return ""


def _load_freq_cache() -> dict[str, int]:
    if not FREQ_CACHE.exists():
        return {}
    return _read_json(FREQ_CACHE)


def _load_kuratierte_gruppen() -> list[dict]:
    gruppen: list[dict] = []
    if not KURATIERTE_GRUPPEN.exists():
        return []
    with open(KURATIERTE_GRUPPEN, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                g = json.loads(line)
            except Exception:
                continue
            if isinstance(g, dict):
                gruppen.append(g)
    return gruppen


def _scale_haeufigkeit(h: int) -> int:
    if h < 1:
        return 1
    if h > 100:
        h = 100
    return (h + 1) // 2


def _load_freq_full() -> dict[str, int]:
    freq: dict[str, int] = {}
    if REIMDB.exists():
        conn = sqlite3.connect(str(REIMDB))
        try:
            cur = conn.execute("SELECT suchwort_norm, haeufigkeit FROM woerter WHERE haeufigkeit IS NOT NULL")
            for w, h in cur:
                if not w or h is None:
                    continue
                if not isinstance(h, int):
                    try:
                        h = int(h)
                    except Exception:
                        continue
                ww = _freq_key(w)
                if ww and ww not in freq:
                    freq[ww] = _scale_haeufigkeit(h)
        finally:
            conn.close()

    cache = _load_freq_cache()
    for w, h in cache.items():
        if w is None or h is None:
            continue
        try:
            hh = int(h)
        except Exception:
            continue
        ww = _freq_key(w)
        if ww and ww not in freq:
            freq[ww] = _scale_haeufigkeit(hh)

    return freq


def _write_freq_full(freq: dict[str, int]):
    items = dict(sorted(freq.items(), key=lambda kv: kv[0]))
    FREQ_FULL_OUT.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def _sanity_check(freq: dict[str, int]):
    print("SANITY freq_full (muss <=8 sein):")
    ok = True
    for w in _SANITY_WORDS:
        h = freq.get(w)
        print(f"  {w}: {h}")
        if h is None or h > 8:
            ok = False
    if not ok:
        raise SystemExit("SANITY-CHECK FAILED (freq_full Skalierung/Quelle falsch)")


def _select_corpus_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    c = conn.execute("SELECT COUNT(*) AS n FROM sprueche WHERE veroeffentlicht = 1").fetchone()["n"]
    if c and int(c) > 0:
        return conn.execute(
            "SELECT spruch, klang_gruppen, reimwoerter FROM sprueche WHERE veroeffentlicht = 1"
        ).fetchall()
    return conn.execute(
        "SELECT spruch, klang_gruppen, reimwoerter FROM sprueche "
        "WHERE judge_score IS NOT NULL ORDER BY judge_score DESC LIMIT 250"
    ).fetchall()


def _extract_endungsfamilien_from_corpus() -> tuple[Counter, int]:
    if not CORPUS_DB.exists():
        return Counter(), 0
    conn = sqlite3.connect(str(CORPUS_DB))
    try:
        rows = _select_corpus_rows(conn)
    finally:
        conn.close()
    fam_counts: Counter = Counter()
    for r in rows:
        used = False
        rw_raw = r["reimwoerter"]
        if rw_raw:
            try:
                rw = json.loads(rw_raw) if isinstance(rw_raw, str) else rw_raw
            except Exception:
                rw = []
            if isinstance(rw, list) and rw:
                for w in rw:
                    if not _looks_alltag(w):
                        continue
                    e = _endung(w)
                    if e:
                        fam_counts[e] += 1
                        used = True
        if not used:
            for line in _split_lines(r["spruch"]):
                parts = line.split()
                if not parts:
                    continue
                w = parts[-1]
                if not _looks_alltag(w):
                    continue
                e = _endung(w)
                if e:
                    fam_counts[e] += 1
    return fam_counts, len(rows)


def _baseline_partner_by_endung(gruppen: list[dict], freq_full: dict[str, int]) -> dict[str, list[tuple[str, int]]]:
    res: dict[str, set[str]] = defaultdict(set)
    for g in gruppen:
        woerter = g.get("woerter") or []
        if not isinstance(woerter, list):
            continue
        for w in woerter:
            if not isinstance(w, dict):
                continue
            ww = w.get("wort")
            if not ww or not _looks_alltag(ww):
                continue
            h = freq_full.get(_freq_key(ww))
            if h is None:
                continue
            e = _endung(ww)
            if e:
                res[e].add(_clean_word(ww))
    out: dict[str, list[tuple[str, int]]] = {}
    for e, ws in res.items():
        lst = []
        for w in ws:
            h = freq_full.get(_freq_key(w))
            if isinstance(h, int):
                lst.append((w, h))
        lst.sort(key=lambda x: (x[1], x[0].casefold()))
        out[e] = lst
    return out


def _candidate_pool_by_endung(freq_full: dict[str, int], families: set[str], max_t: int) -> dict[str, list[tuple[str, int]]]:
    pool: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for w, h in freq_full.items():
        if not isinstance(h, int) or h > max_t:
            continue
        if not _looks_alltag(w):
            continue
        e = _endung(w)
        if not e or e not in families:
            continue
        pool[e].append((_clean_word(w), int(h)))
    for e in pool:
        pool[e].sort(key=lambda x: (x[1], x[0].casefold()))
    return dict(pool)


def _count_by_threshold(words: list[tuple[str, int]]) -> dict[int, int]:
    out: dict[int, int] = {}
    for t in THRESHOLDS:
        out[t] = sum(1 for _, h in words if h <= t)
    return out


def _build_reimpool_json(pool: dict[str, list[tuple[str, int]]]) -> list[dict]:
    out: list[dict] = []
    for endung in sorted(pool):
        for w, h in pool[endung]:
            out.append({"wort": w, "reim_endung": endung, "haeufigkeit": int(h), "quelle": "R4-build1b"})
    out.sort(key=lambda e: (e["reim_endung"], e["haeufigkeit"], e["wort"].casefold()))
    return out


def _write_reimpool(entries: list[dict]):
    REIMPOOL_OUT.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_sweep(
    corpus_size: int,
    fam_counts: Counter,
    baseline_by_endung: dict[str, list[tuple[str, int]]],
    pool_after: dict[str, list[tuple[str, int]]],
):
    lines: list[str] = []
    lines.append("# R.4-data-1b Sweep: gelaeufige Partner pro Endungsfamilie")
    lines.append("")
    lines.append("## SANITY (Frequenzquelle)")
    lines.append("")
    lines.append("Worte muessen <=8 sein: Witz/Blitz/Sitz/Spitze/Katze/Platz.")
    lines.append("")
    lines.append("## Tabelle (NACHHER, Pool=freq_full.json, Gate=haeufigkeit<=T)")
    lines.append("")
    lines.append("| Endung | Korpus-Treffer | P<=8 | P<=12 | P<=15 | P<=18 | knapp_aber_bekannt |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")

    fam_ge12 = {t: 0 for t in THRESHOLDS}
    knapp_list: list[str] = []

    for endung in sorted(fam_counts, key=lambda k: (-fam_counts[k], k)):
        words_after = pool_after.get(endung, [])
        c = _count_by_threshold(words_after)
        for t in THRESHOLDS:
            if c[t] >= TARGET_MIN_PARTNER:
                fam_ge12[t] += 1
        knapp = "ja" if (c[18] < TARGET_MIN_PARTNER and endung in _KNAPP_ABER_BEKANNT) else "nein"
        if knapp == "ja":
            knapp_list.append(endung)
        lines.append(
            f"| {endung} | {fam_counts[endung]} | {c[8]} | {c[12]} | {c[15]} | {c[18]} | {knapp} |"
        )

    lines.append("")
    lines.append(
        "Familien mit >=12 Partnern: "
        + ", ".join([f"<={t}: {fam_ge12[t]}" for t in THRESHOLDS])
        + f" (von {len(fam_counts)})"
    )

    if knapp_list:
        lines.append("")
        lines.append("knapp_aber_bekannt:")
        lines.append("")
        lines.append(", ".join(sorted(set(knapp_list))))

    SWEEP_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    freq_full = _load_freq_full()
    _write_freq_full(freq_full)
    _sanity_check(freq_full)

    fam_counts, corpus_size = _extract_endungsfamilien_from_corpus()
    families = set(fam_counts.keys())
    gruppen = _load_kuratierte_gruppen()
    baseline_by_endung = _baseline_partner_by_endung(gruppen, freq_full)

    pool_after = _candidate_pool_by_endung(freq_full, families=families, max_t=MAX_T)
    reimpool_entries = _build_reimpool_json(pool_after)
    _write_reimpool(reimpool_entries)

    _write_sweep(corpus_size, fam_counts, baseline_by_endung, pool_after)

    fam_ge12 = {t: 0 for t in THRESHOLDS}
    for endung in fam_counts:
        c = _count_by_threshold(pool_after.get(endung, []))
        for t in THRESHOLDS:
            if c[t] >= TARGET_MIN_PARTNER:
                fam_ge12[t] += 1
    print("Familien mit >=12 Partnern:", ", ".join([f"<={t}: {fam_ge12[t]}" for t in THRESHOLDS]))


if __name__ == '__main__':
    main()
