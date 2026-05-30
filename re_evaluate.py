"""
re_evaluate.py
Reimgruppen strukturieren und stabilisieren – KEINE API Calls, KEIN Re-Scoring.

Schritte:
  1. Klang-Naming korrigieren
  2. Reimqualität prüfen (falsche Reime raus)
  2b. Silben-Konsistenz-Filter (dominante Silbenzahl, Ausreißer raus)
  3. Gruppengröße normalisieren (4–5 Wörter anstreben)
  4. Kleine Gruppen markieren (group_type: small / normal)
  5. group_score aktualisieren wenn Wörter entfernt wurden
"""

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

try:
    from reim_scraper import RESULTS_FILE, JSON_EXPORT_FILE, _merge_duplicate_gruppen
    CLEAN_OUTPUT = RESULTS_FILE.parent / "reimgruppen_clean.jsonl"
except ImportError:
    RESULTS_FILE = Path("reimgruppen.jsonl")
    JSON_EXPORT_FILE = Path("reimgruppen_export.json")
    CLEAN_OUTPUT = Path("reimgruppen_clean.jsonl")

    def _merge_duplicate_gruppen(gruppen):
        return gruppen


# ── Konfiguration ─────────────────────────────────────────────────────────────

# Klang-Korrekturen: unscharfe Buchstabenreste → phonetisch saubere Reimkerne
KLANG_FIXES = {
    "uf": "huf",
    "i":  "ier",
    # Keine generischen Kurzformen wie "ei" – zu viele falsche Treffer
}

# Wörter die nachweislich NICHT zu ihrem Klang-Kern passen
# Format: { klang_nach_fix: {wörter_die_rausmüssen} }
FALSE_RHYMES: dict[str, set[str]] = {
    "und":  {"Wunde"},     # -unde ≠ -und
    "eib":  {"Speiben"},   # -eiben ≠ -eib
    "ier":  {},
    "huf":  {"Ruf"},       # -uf ohne h-Laut passt nicht in Huf-Gruppe
    "eis":  {},
    "echt": {},
}

# Erweiterungspool: mögliche neue Wörter pro Klang-Kern
# Werden nur hinzugefürt wenn Gruppe < 4 Wörter hat
# Format: { klang: [ {wort, silben, score, tags, kontext} ] }
EXPANSION_POOL: dict[str, list[dict]] = {
    "ohr": [
        {"wort": "Moor",  "silben": 1, "score": 4, "tags": ["laendlich"],          "kontext": ["natur", "bauernhof"]},
        {"wort": "Tor",   "silben": 1, "score": 3, "tags": ["laendlich"],          "kontext": ["bauernhof", "dorf"]},
    ],
    "eif": [
        {"wort": "Greif", "silben": 1, "score": 3, "tags": ["laendlich"],          "kontext": ["natur", "dorf"]},
    ],
    "ust": [
        {"wort": "Knust", "silben": 1, "score": 4, "tags": ["laendlich"],          "kontext": ["essen", "bauernhof"]},
    ],
    "aufen": [
        {"wort": "schnaufen", "silben": 2, "score": 4, "tags": ["derb", "laendlich"], "kontext": ["tier", "arbeit"]},
    ],
    "eib": [
        {"wort": "Zeitvertreib", "silben": 3, "score": 3, "tags": [],              "kontext": ["dorf", "beziehung"]},
    ],
    "huf": [
        # Reimraum ist begrenzt – lieber klein lassen
    ],
}

MIN_GROUP_SIZE = 4        # Ziel-Minimum
MAX_GROUP_SIZE = 5        # Ziel-Maximum
SMALL_GROUP_THRESHOLD = 3  # ≤ this → group_type: "small"
VERBOSE = False            # True → Debug-Prints pro Gruppe nach Expansion


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def safe_score(val, default=3):
    try:
        s = int(val)
        return max(1, min(5, s))
    except (TypeError, ValueError):
        return default


def calc_group_score(woerter: list[dict]) -> float:
    scores = [safe_score(w.get("score") or w.get("pointe_score")) for w in woerter]
    return round(sum(scores) / len(scores), 1) if scores else 0.0


def is_valid_rhyme(klang: str, wort: str) -> bool:
    """Prüft ob ein Wort auf den Klang-Kern endet (Buchstaben-Endungsregel).
    Kombiniert mit der harten FALSE_RHYMES-Blacklist in remove_false_rhymes.
    """
    return wort.lower().endswith(klang.lower())


def create_backup(path: Path):
    if not path.exists():
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_suffix(f".backup_{ts}{path.suffix}")
    shutil.copy(path, backup)
    print(f"  [BACKUP] {backup}")


def load_gruppen(path: Path) -> list[dict]:
    gruppen = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    gruppen.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"  [WARN] Parse-Fehler: {e}")
    return gruppen


# ── Schritt 1: Klang-Naming korrigieren ───────────────────────────────────────

def fix_klang(gruppen: list[dict]) -> tuple[list[dict], int]:
    fixed = 0
    for g in gruppen:
        old = g.get("klang", "")
        new = KLANG_FIXES.get(old, old)
        if new != old:
            print(f"  [KLANG] '{old}' → '{new}'  (suchwort: {g.get('suchwort','')})")
            g["klang"] = new
            fixed += 1
    return gruppen, fixed


# ── Schritt 2: Falsche Reime entfernen ────────────────────────────────────────

def remove_false_rhymes(gruppen: list[dict]) -> tuple[list[dict], int, set[str]]:
    """Entfernt Wörter die nicht zum Klang-Kern passen.
    Zwei Stufen: harte Blacklist (FALSE_RHYMES) + phonetische Endungsregel.
    """
    removed = 0
    removed_klang: set[str] = set()
    for g in gruppen:
        klang = g.get("klang", "")
        blacklist = FALSE_RHYMES.get(klang, set())
        before = len(g["woerter"])

        def keep(w, _klang=klang, _bl=blacklist):
            wort = w.get("wort", "")
            if wort in _bl:
                return False
            if not is_valid_rhyme(_klang, wort):
                return False
            return True

        g["woerter"] = [w for w in g["woerter"] if keep(w)]
        delta = before - len(g["woerter"])
        if delta:
            removed += delta
            removed_klang.add(klang)
            print(f"  [REIM]  '{klang}': {delta} Wort/Wörter entfernt (Blacklist oder Endungsregel)")
    return gruppen, removed, removed_klang


# ── Schritt 2b: Silben-Konsistenz-Filter ──────────────────────────────────────

def _count_silben(wort: str) -> int:
    vokale = "aeiouäöüy"
    s, prev = 0, False
    for ch in wort.lower():
        is_v = ch in vokale
        if is_v and not prev:
            s += 1
        prev = is_v
    return max(1, s)


def filter_by_silben(gruppen: list[dict]) -> tuple[list[dict], int, set[str]]:
    """Pro Gruppe: dominante Silbenzahl bestimmen (Mehrheit der Wörter).
    Alle Wörter die nicht exakt diese Silbenzahl haben → raus.
    """
    from collections import Counter
    total_removed = 0
    modified_klang: set[str] = set()
    for g in gruppen:
        woerter = g.get("woerter", [])
        if not woerter:
            continue

        silben_count = Counter(_count_silben(w.get("wort", "")) for w in woerter)
        dominante_silben = silben_count.most_common(1)[0][0]

        vorher = len(woerter)
        g["woerter"] = [w for w in woerter if _count_silben(w.get("wort", "")) == dominante_silben]
        g["silben"] = dominante_silben

        nachher = len(g["woerter"])
        if vorher != nachher:
            removed = vorher - nachher
            total_removed += removed
            modified_klang.add(g["klang"])
            print(f"  [SILBEN] '{g['klang']}': {vorher} → {nachher} Wörter (dominante Silben: {dominante_silben}, {removed} entfernt)")

    return gruppen, total_removed, modified_klang


# ── Schritt 3: Gruppen auffüllen (nur wenn < MIN_GROUP_SIZE) ─────────────────

def expand_groups(gruppen: list[dict]) -> tuple[list[dict], int]:
    added = 0
    for g in gruppen:
        klang = g.get("klang", "")
        current = len(g["woerter"])
        if current >= MIN_GROUP_SIZE:
            continue

        candidates = EXPANSION_POOL.get(klang, [])
        existing_words = {w.get("wort", "").lower() for w in g["woerter"]}
        slots = MIN_GROUP_SIZE - current

        for candidate in candidates:
            if slots <= 0:
                break
            wort = candidate["wort"]
            if wort.lower() in existing_words:
                continue
            # Sicherheitscheck 1: phonetische Endung muss passen
            if not is_valid_rhyme(klang, wort):
                print(f"  [SKIP]  '{klang}' ← '{wort}' passt phonetisch nicht")
                continue
            # Sicherheitscheck 2: kein Müll aus dem Pool (Score-Minimum)
            if safe_score(candidate.get("score")) < 3:
                print(f"  [SKIP]  '{klang}' ← '{wort}' Score zu niedrig ({candidate.get('score')})")
                continue
            g["woerter"].append(candidate)
            existing_words.add(candidate["wort"].lower())
            print(f"  [FILL]  '{klang}' ← '{candidate['wort']}' (score {candidate['score']})")
            added += 1
            slots -= 1

        if VERBOSE:
            print(f"  [DEBUG] '{klang}' → jetzt {len(g['woerter'])} Wörter")

    return gruppen, added


# ── Schritt 4: group_type setzen ──────────────────────────────────────────────

def set_group_type(gruppen: list[dict]) -> list[dict]:
    for g in gruppen:
        size = len(g.get("woerter", []))
        g["group_type"] = "small" if size <= SMALL_GROUP_THRESHOLD else "normal"
    return gruppen


# ── Schritt 5: group_score aktualisieren (nur wenn Wörter entfernt) ───────────

def update_group_scores(gruppen: list[dict], modified_klang: set[str]) -> list[dict]:
    """Berechnet group_score nur neu wenn:
    - group_score fehlt (erster Lauf), ODER
    - Wörter tatsächlich verändert wurden (removed_flag via modified_klang)
    "small" ≠ "verändert" – deshalb kein group_type-Check.
    """
    for g in gruppen:
        woerter = g.get("woerter", [])
        if not woerter:
            continue
        klang = g.get("klang", "")
        if "group_score" not in g or klang in modified_klang:
            g["group_score"] = calc_group_score(woerter)
    return gruppen


# ── Ausgabe ───────────────────────────────────────────────────────────────────

def write_output(gruppen: list[dict], jsonl_path: Path):
    create_backup(jsonl_path)
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for g in gruppen:
            f.write(json.dumps(g, ensure_ascii=False) + "\n")

    merged = _merge_duplicate_gruppen(gruppen)
    with open(JSON_EXPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    return merged


def print_summary(gruppen: list[dict]):
    total_words = sum(len(g["woerter"]) for g in gruppen)
    small = [g for g in gruppen if g.get("group_type") == "small"]
    normal = [g for g in gruppen if g.get("group_type") == "normal"]

    print()
    print("─" * 50)
    print(f"  Gruppen gesamt : {len(gruppen)}")
    print(f"  → normal       : {len(normal)}")
    print(f"  → small        : {len(small)}")
    print(f"  Wörter gesamt  : {total_words}")
    print(f"  Ø Wörter/Gruppe: {total_words / len(gruppen):.1f}")
    print()
    print(f"  {'Klang':<10} {'n':>3}  {'score':>6}  {'type':<8}  Wörter")
    print("  " + "─" * 65)
    for g in sorted(gruppen, key=lambda x: (-x.get("group_score", 0), x["klang"])):
        ws = ", ".join(w.get("wort", "?") for w in g["woerter"])
        print(f"  {g['klang']:<10} {len(g['woerter']):>3}  {g.get('group_score', 0):>6}  {g.get('group_type','?'):<8}  {ws}")
    print("─" * 50)


# ── Entry Point ───────────────────────────────────────────────────────────────

def re_evaluate(input_path=None, output_path=None):
    src = Path(input_path) if input_path else RESULTS_FILE
    dst = Path(output_path) if output_path else CLEAN_OUTPUT

    print("=" * 50)
    print("  RE-EVALUATE: Strukturbereinigung")
    print("  (keine API-Calls, kein Re-Scoring)")
    print("=" * 50)
    print(f"  Eingabe : {src}")
    print(f"  Ausgabe : {dst}")
    print()

    if not src.exists():
        print(f"  FEHLER: {src} nicht gefunden!")
        sys.exit(1)

    gruppen = load_gruppen(src)
    print(f"  Geladen: {len(gruppen)} Gruppen\n")

    print("── Schritt 1: Klang-Naming ──────────────────────")
    gruppen, n_fixed = fix_klang(gruppen)
    print(f"  {n_fixed} Klang-Name(n) korrigiert\n")

    print("── Schritt 2: Falsche Reime entfernen ───────────")
    gruppen, n_removed, removed_klang = remove_false_rhymes(gruppen)
    gruppen_vorher = len(gruppen)
    gruppen = [g for g in gruppen if len(g.get("woerter", [])) > 0]
    n_leer = gruppen_vorher - len(gruppen)
    if n_leer:
        print(f"  [{n_leer} leere Gruppe(n) entfernt]")
    print(f"  {n_removed} Wort/Wörter entfernt\n")

    print("── Schritt 2b: Silben-Konsistenz-Filter ─────────")
    gruppen, n_silben_removed, silben_klang = filter_by_silben(gruppen)
    removed_klang = removed_klang | silben_klang
    print(f"  {n_silben_removed} Wort/Wörter durch Silben-Filter entfernt\n")

    print("── Schritt 3: Gruppen auffüllen ─────────────────")
    gruppen, n_added = expand_groups(gruppen)
    print(f"  {n_added} Wort/Wörter ergänzt\n")

    print("── Schritt 4: group_type setzen ─────────────────")
    gruppen = set_group_type(gruppen)
    small_count = sum(1 for g in gruppen if g["group_type"] == "small")
    print(f"  {small_count} Gruppe(n) als 'small' markiert\n")

    print("── Schritt 5: group_score aktualisieren ─────────")
    gruppen = update_group_scores(gruppen, modified_klang=removed_klang)
    print(f"  Fertig\n")

    print("── Ausgabe schreiben ─────────────────────────────")
    merged = write_output(gruppen, dst)
    print(f"  {len(gruppen)} Gruppen → JSONL")
    print(f"  {len(merged)} Gruppen → JSON (nach Merge)")

    print_summary(gruppen)
    print("  FERTIG")

    return gruppen


if __name__ == "__main__":
    inp = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    re_evaluate(input_path=inp, output_path=out)
