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

REIMPOOL_OUT = OUT_DIR / "reimpool_R4.json"
COVERAGE_OUT = OUT_DIR / "reimpool_R4_coverage.md"

TARGET_HAEUF = 8
TARGET_MIN_PARTNER = 12

_TRAIL_PUNCT_RE = re.compile(r"^[\(\[\{\"'“”‘’]+|[\)\]\}\"'“”‘’,.!?;:]+$")
_WORD_OK_RE = re.compile(r"^[A-Za-zÄÖÜäöüß]+$")
_FREMD_ENDINGS = (
    "tion", "sion", "ment", "ance", "ence", "ität",
    "ismus", "ieren", "abel", "ibel", "thek", "pell",
)


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


def _baseline_common_partner(gruppen: list[dict], freq_cache: dict[str, int]) -> dict[str, set[str]]:
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
            h = freq_cache.get(_freq_key(ww))
            if h is None or h > TARGET_HAEUF:
                continue
            e = _endung(ww)
            if e:
                res[e].add(_clean_word(ww))
    return dict(res)


def _candidate_pool_by_endung(freq_cache: dict[str, int]) -> dict[str, list[tuple[str, int]]]:
    pool: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for w, h in freq_cache.items():
        if not isinstance(h, int) or h > TARGET_HAEUF:
            continue
        if not _looks_alltag(w):
            continue
        e = _endung(w)
        if not e:
            continue
        pool[e].append((_clean_word(w), int(h)))
    for e in pool:
        pool[e].sort(key=lambda x: (x[1], x[0].casefold()))
    return dict(pool)


def _build_reimpool_additions(
    fam_counts: Counter,
    baseline_common: dict[str, set[str]],
    freq_cache: dict[str, int],
) -> list[dict]:
    additions: list[dict] = []
    pool = _candidate_pool_by_endung(freq_cache)
    for endung in sorted(fam_counts):
        base = baseline_common.get(endung, set())
        if len(base) >= TARGET_MIN_PARTNER:
            continue
        need = TARGET_MIN_PARTNER - len(base)
        cand = pool.get(endung, [])
        picked: list[tuple[str, int]] = []
        seen = {w.casefold() for w in base}
        for w, h in cand:
            if w.casefold() in seen:
                continue
            picked.append((w, h))
            seen.add(w.casefold())
            if len(picked) >= need:
                break
        for w, h in picked:
            additions.append({"wort": w, "reim_endung": endung, "haeufigkeit": int(h), "quelle": "R4-build1"})
    additions.sort(key=lambda e: (e["reim_endung"], e["haeufigkeit"], e["wort"].casefold()))
    return additions


def _count_after(
    fam_counts: Counter,
    baseline_common: dict[str, set[str]],
    additions: list[dict],
) -> dict[str, int]:
    add_map: dict[str, set[str]] = defaultdict(set)
    for e in additions:
        add_map[e["reim_endung"]].add(e["wort"].casefold())
    after: dict[str, int] = {}
    for endung in fam_counts:
        before = baseline_common.get(endung, set())
        after[endung] = len({w.casefold() for w in before} | add_map.get(endung, set()))
    return after


def _write_reimpool(additions: list[dict]):
    REIMPOOL_OUT.write_text(json.dumps(additions, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_coverage(
    corpus_size: int,
    fam_counts: Counter,
    baseline_common: dict[str, set[str]],
    additions: list[dict],
):
    after_counts = _count_after(fam_counts, baseline_common, additions)
    add_by_klang = Counter(e["reim_endung"] for e in additions)

    lines: list[str] = []
    lines.append("# R.4-data-1 Coverage: gelaeufige Reimpartner (<= 8)")
    lines.append("")
    lines.append("## VORARBEIT")
    lines.append("")
    lines.append("- Reimwoerter im Generator kommen aus `output/reimgruppen_derb.jsonl` (kuratierte Gruppen) und Seeds aus `output/seed_woerter_v22.json`.")
    lines.append("- Die Wort-Haeufigkeit steht als Integer-Feld `haeufigkeit` (1=sehr haeufig/alltaeglich ... 100=selten/exotisch).")
    lines.append("- Gate: fuer diesen Build gilt haeufigkeit<=8; Worte ohne Haeufigkeit gelten als exotisch und sind ausgeschlossen.")
    lines.append("- Korpus-Endungsfamilien: aus `output/sprueche.db` (veroeffentlicht=1; Fallback: Top-250 nach judge_score), Endung ueber generator._reim_endung(Zeilenendwort/Reimwort).")
    lines.append("- Neue Kandidaten werden aus `output/derewo_freq_cache.json` (DeReWo-Cache) gezogen, ebenfalls ueber Endung gruppiert.")
    lines.append("")
    lines.append(f"Korpus: {corpus_size} Sprueche, {len(fam_counts)} Endungsfamilien.")
    lines.append("")
    lines.append("## Tabelle")
    lines.append("")
    lines.append("| Endungsfamilie | Korpus-Treffer | Partner<=8 VORHER | Partner<=8 NACHHER | Neu hinzugefuegt | <12 nachher? |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for endung in sorted(fam_counts, key=lambda k: (-fam_counts[k], k)):
        before = len(baseline_common.get(endung, set()))
        after = after_counts.get(endung, before)
        added = int(add_by_klang.get(endung, 0))
        still = "ja" if after < TARGET_MIN_PARTNER else "nein"
        lines.append(f"| {endung} | {fam_counts[endung]} | {before} | {after} | {added} | {still} |")

    before_bad = sum(1 for endung in fam_counts if len(baseline_common.get(endung, set())) < TARGET_MIN_PARTNER)
    after_bad = sum(1 for endung in fam_counts if after_counts.get(endung, 0) < TARGET_MIN_PARTNER)
    lines.append("")
    lines.append(f"Familien < {TARGET_MIN_PARTNER} gelaeufige Partner: VORHER {before_bad} -> NACHHER {after_bad}")

    COVERAGE_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    freq_cache = _load_freq_cache()
    gruppen = _load_kuratierte_gruppen()
    fam_counts, corpus_size = _extract_endungsfamilien_from_corpus()
    baseline_common = _baseline_common_partner(gruppen, freq_cache)
    additions = _build_reimpool_additions(fam_counts, baseline_common, freq_cache)
    _write_reimpool(additions)
    _write_coverage(corpus_size, fam_counts, baseline_common, additions)

    before_bad = sum(1 for endung in fam_counts if len(baseline_common.get(endung, set())) < TARGET_MIN_PARTNER)
    after_counts = _count_after(fam_counts, baseline_common, additions)
    after_bad = sum(1 for endung in fam_counts if after_counts.get(endung, 0) < TARGET_MIN_PARTNER)
    print(f"Familien < {TARGET_MIN_PARTNER} gelaeufige Partner: VORHER {before_bad} -> NACHHER {after_bad}")


if __name__ == '__main__':
    main()
