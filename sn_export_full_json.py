import json
import io
import re
import zipfile
import requests
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(r"C:\Users\miron\Documents\trae_projects\scrape")
OUTPUT_DIR = BASE_DIR / "output"
SNAPSHOT_F = OUTPUT_DIR / "sprachnudel_raw.snapshot.v11.merged.jsonl"
EXPORT_F = OUTPUT_DIR / "sprachnudel_export.v12.json"
DEREWO_URL = "http://www.ids-mannheim.de/fileadmin/kl/derewo/derewo-v-ww-bll-320000g-2012-12-31-1.0.zip"
DEREWO_CACHE = OUTPUT_DIR / "derewo_freq_cache.json"
WIKTIONARY_DATA = OUTPUT_DIR / "wiktionary_data.json"

WORTART_ORDER = ["allgemein", "adjektive", "substantive", "verben", "unbekannt"]
VOWELS = set("aeiouäöüy")


def count_silben(wort: str) -> int:
    count = 0
    prev = False
    for ch in wort.lower():
        is_v = ch in VOWELS
        if is_v and not prev:
            count += 1
        prev = is_v
    return max(1, count)


def load_derewo_freq() -> dict[str, int]:
    if DEREWO_CACHE.exists():
        with open(DEREWO_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    print("Lade DeReWo-Haeufigkeiten aus ZIP...")
    r = requests.get(DEREWO_URL, timeout=60)
    r.raise_for_status()
    data = None
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        for name in z.namelist():
            if name.endswith(".txt"):
                data = z.read(name).decode("latin-1", errors="replace")
                break
    freq = {}
    if data:
        for line in data.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if not parts:
                continue
            word = parts[0]
            if not re.match(r'^[a-zA-ZäöüÄÖÜß]+$', word):
                continue
            if ',' in word or '(' in word or ')' in word:
                continue
            fc = int(parts[1]) if len(parts) > 1 else 99
            key = word.casefold()
            if key not in freq or fc < freq[key]:
                freq[key] = fc
    print(f"DeReWo: {len(freq)} Woerter mit Haeufigkeit geladen")
    with open(DEREWO_CACHE, "w", encoding="utf-8") as f:
        json.dump(freq, f, ensure_ascii=False)
    return freq


def load_wiktionary_data() -> dict:
    if WIKTIONARY_DATA.exists():
        print(f"Lade Wiktionary-Daten aus {WIKTIONARY_DATA.name}...")
        with open(WIKTIONARY_DATA, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"  {len(data)} Woerter mit Wiktionary-Daten")
        return data
    print(f"WARNUNG: {WIKTIONARY_DATA.name} nicht gefunden – exportiere ohne Wiktionary-Daten")
    return {}


def compute_semantic_score(word_cf: str, rhyme_word_cf: str, wt: dict) -> float:
    """
    Berechnet einen Semantik-Score (0.0-1.0) zwischen zwei Woertern.
    Beruecksichtigt: Synonyme, Antonyme, verwandte Woerter, gleiche Themen.
    """
    score = 0.0
    reasons = []

    word_data = wt.get(word_cf, {})
    rhyme_data = wt.get(rhyme_word_cf, {})

    # Synonym-Check
    word_syns = {s.casefold() for s in word_data.get("synonyme", [])}
    rhyme_syns = {s.casefold() for s in rhyme_data.get("synonyme", [])}

    if rhyme_word_cf in word_syns:
        score += 0.5
        reasons.append("Synonym")
    elif word_syns & rhyme_syns:
        score += 0.3
        reasons.append("Gemeinsame Synonyme")

    # Antonym-Check
    word_ants = {a.casefold() for a in word_data.get("antonyme", [])}
    if rhyme_word_cf in word_ants:
        score += 0.4
        reasons.append("Antonym")

    # Verwandte Woerter
    word_rel = {r.casefold() for r in word_data.get("verwandte", [])}
    rhyme_rel = {r.casefold() for r in rhyme_data.get("verwandte", [])}
    if rhyme_word_cf in word_rel:
        score += 0.3
        reasons.append("Verwandt")
    elif word_rel & rhyme_rel:
        score += 0.15
        reasons.append("Gemeinsame Verwandte")

    # Gleiche Themen
    word_topics = set(word_data.get("themen", []))
    rhyme_topics = set(rhyme_data.get("themen", []))
    shared_topics = word_topics & rhyme_topics
    if shared_topics:
        topic_score = min(0.3, len(shared_topics) * 0.1)
        score += topic_score
        reasons.append(f"{len(shared_topics)} gemeinsame Themen")

    # Gleiche Wortart
    word_pos = word_data.get("wortart", "")
    rhyme_pos = rhyme_data.get("wortart", "")
    if word_pos and rhyme_pos:
        wp = set(word_pos) if isinstance(word_pos, list) else {word_pos}
        rp = set(rhyme_pos) if isinstance(rhyme_pos, list) else {rhyme_pos}
        if wp & rp:
            score += 0.05
            reasons.append("Gleiche Wortart")

    return min(1.0, score), reasons


def group_results(results: list[dict]) -> list[dict]:
    by_wortart: dict[str, dict[int, list[str]]] = {}
    seen_per_group: set[tuple[str, int, str]] = set()

    for item in results:
        wort = (item.get("wort") or "").strip()
        if not wort:
            continue
        wortart = (item.get("wortart") or "unbekannt").lower()
        if wortart == "suchwort":
            continue
        if wortart not in WORTART_ORDER:
            wortart = "unbekannt"
        silben = int(item.get("silben") or count_silben(wort))

        by_wortart.setdefault(wortart, {})
        by_wortart[wortart].setdefault(silben, [])

        key = (wortart, silben, wort.casefold())
        if key in seen_per_group:
            continue
        seen_per_group.add(key)
        by_wortart[wortart][silben].append(wort)

    kategorien = []
    for wortart in WORTART_ORDER:
        if wortart not in by_wortart:
            continue
        gruppen = [
            {"silben": silben, "woerter": woerter}
            for silben, woerter in sorted(by_wortart[wortart].items(), reverse=True)
        ]
        kategorien.append({"wortart": wortart, "gruppen": gruppen})
    return kategorien


def build_word_entry(entry: dict, derewo_freq: dict[str, int], wt: dict) -> dict:
    suchwort = entry.get("suchwort") or ""
    suchwort_cf = suchwort.strip().casefold()
    raw_results = entry.get("results", [])
    rhymes = [
        item for item in raw_results
        if (item.get("wortart") or "").lower() != "suchwort"
    ]
    reim_count = int(entry.get("count", 0) or 0)

    # Wiktionary-Daten fuer das Suchwort
    wt_data = wt.get(suchwort_cf, {})

    # Semantik-Score fuer Reimwoerter berechnen
    enriched_rhymes = []
    for item in rhymes:
        rhyme_word = (item.get("wort") or "").strip()
        enriched = dict(item)
        if rhyme_word:
            sem_score, sem_reasons = compute_semantic_score(
                suchwort_cf, rhyme_word.casefold(), wt
            )
            if sem_score > 0:
                enriched["semantik_score"] = round(sem_score, 2)
                enriched["semantik_gruende"] = sem_reasons
            # DeReWo-Haeufigkeit pro Reimpartner (1=alltaeglich ... 100=exotisch)
            enriched["haeufigkeit"] = derewo_freq.get(rhyme_word.casefold(), 100)
        enriched_rhymes.append(enriched)

    # Reime nach Semantik-Score sortieren (hoechste zuerst)
    enriched_rhymes.sort(
        key=lambda r: (-r.get("semantik_score", 0), r.get("wort", "")),
    )

    # Thematisch gruppierte Reime
    themed_rhymes = build_themed_rhymes(suchwort_cf, enriched_rhymes, wt)

    result = {
        "suchwort": suchwort,
        "suchwort_norm": suchwort_cf,
        "klang": entry.get("klang"),
        "suchwort_silben": int(entry.get("suchwort_silben") or count_silben(suchwort)),
        "reim_count": reim_count,
        "hat_reime": reim_count > 0,
        "haeufigkeit": derewo_freq.get(suchwort_cf, 100),
        "source_url": entry.get("source_url"),
        "scraped_at": entry.get("scraped_at"),
        "raw_results": raw_results,
        "rhymes": enriched_rhymes,
        "kategorien": group_results(raw_results),
    }

    # Wiktionary-Felder hinzufuegen
    if "wortart" in wt_data:
        result["wortart"] = wt_data["wortart"]
    if "synonyme" in wt_data:
        result["synonyme"] = wt_data["synonyme"]
    if "antonyme" in wt_data:
        result["antonyme"] = wt_data["antonyme"]
    if "verwandte" in wt_data:
        result["verwandte"] = wt_data["verwandte"]
    if "abgeleitete" in wt_data:
        result["abgeleitete"] = wt_data["abgeleitete"]
    if "themen" in wt_data:
        result["themen"] = wt_data["themen"]
    if "ipa" in wt_data:
        result["ipa"] = wt_data["ipa"]
    if "definitionen" in wt_data:
        result["definitionen"] = wt_data["definitionen"]
    if themed_rhymes:
        result["themed_rhymes"] = themed_rhymes

    return result


def build_themed_rhymes(suchwort_cf: str, rhymes: list[dict], wt: dict) -> list[dict]:
    """Gruppiert Reimwoerter nach semantischen Themen."""
    wt_data = wt.get(suchwort_cf, {})
    suchwort_topics = set(wt_data.get("themen", []))

    if not suchwort_topics:
        return []

    themed = {}
    unthemed = []

    for rhyme in rhymes:
        rhyme_word = (rhyme.get("wort") or "").strip()
        if not rhyme_word:
            continue
        rhyme_data = wt.get(rhyme_word.casefold(), {})
        rhyme_topics = set(rhyme_data.get("themen", []))
        shared = suchwort_topics & rhyme_topics

        if shared:
            for topic in shared:
                themed.setdefault(topic, []).append(rhyme_word)
        else:
            unthemed.append(rhyme_word)

    result = [
        {"thema": topic, "woerter": woerter}
        for topic, woerter in sorted(themed.items(), key=lambda x: -len(x[1]))
    ]
    if unthemed:
        result.append({"thema": "Weitere", "woerter": unthemed[:50]})

    return result


def main():
    derewo_freq = load_derewo_freq()
    wt = load_wiktionary_data()
    words = []
    with_rhymes = 0
    with_pos = 0
    with_syn = 0
    with_topics = 0
    with_semantic_score = 0
    total_rhyme_relations = 0

    print("Exportiere Woerter...")
    with open(SNAPSHOT_F, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            word_entry = build_word_entry(entry, derewo_freq, wt)
            words.append(word_entry)
            if word_entry["hat_reime"]:
                with_rhymes += 1
            if "wortart" in word_entry:
                with_pos += 1
            if "synonyme" in word_entry:
                with_syn += 1
            if "themen" in word_entry:
                with_topics += 1
            for r in word_entry.get("rhymes", []):
                if r.get("semantik_score", 0) > 0:
                    with_semantic_score += 1
            total_rhyme_relations += word_entry["reim_count"]
            if (i + 1) % 10000 == 0:
                print(f"  {i+1} Woerter verarbeitet...")

    export = {
        "schema_version": 2,
        "export_kind": "sprachnudel_full_export_with_semantics",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_snapshot": SNAPSHOT_F.name,
        "stats": {
            "word_count": len(words),
            "words_with_rhymes": with_rhymes,
            "words_without_rhymes": len(words) - with_rhymes,
            "total_rhyme_relations": total_rhyme_relations,
            "words_with_pos": with_pos,
            "words_with_synonyms": with_syn,
            "words_with_topics": with_topics,
            "rhymes_with_semantic_score": with_semantic_score,
        },
        "words": words,
    }

    with open(EXPORT_F, "w", encoding="utf-8") as f:
        json.dump(export, f, ensure_ascii=False)

    size_mb = EXPORT_F.stat().st_size / 1024 / 1024
    print(f"\nExport geschrieben: {EXPORT_F}")
    print(f"Dateigroesse: {size_mb:.1f} MB")
    print(f"Woerter: {len(words)}")
    print(f"Mit Reimen: {with_rhymes}")
    print(f"Ohne Reime: {len(words) - with_rhymes}")
    print(f"Reimbeziehungen: {total_rhyme_relations}")
    print(f"Mit Wortart: {with_pos}")
    print(f"Mit Synonymen: {with_syn}")
    print(f"Mit Themen: {with_topics}")
    print(f"Reime mit Semantik-Score: {with_semantic_score}")


if __name__ == "__main__":
    main()
