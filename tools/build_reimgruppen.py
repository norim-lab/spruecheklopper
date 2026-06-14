"""build_reimgruppen.py — kuratierter Reimgruppen-Build aus sprachnudel_export.v12.json.

Liest die 424 MB Voll-DB einmalig (gestreamt via ijson) und erzeugt:
  1. output/reimgruppen_derb.jsonl  (eine JSON-Zeile pro Klanggruppe)
  2. output/seed_woerter_v22.json   (Seed-Liste, sortiert nach Haeufigkeit)

generator.py / app.py werden NICHT angefasst — reiner Daten-Build.

Filter-Regeln (siehe AUFGABE):
  - haeufigkeit 1 = sehr haeufig, 100 = exotisch. KLEINER = haeufiger.
  - Wort-Filter: hat_reime, reim_count >= 4, haeufigkeit <= 30,
                 silben in 1..4, wortart in {Substantiv, Verb, Adjektiv}
  - Partner-Filter: haeufigkeit <= 30, silben in 1..4, Wortart-Whitelist
  - Gruppe behalten ab >= 4 Partner nach Merge pro klang.
  - FREMDWORT-/EXOTIK-FILTER (Wort UND Partner, case-insensitive):
      * Suffix-Blacklist: -ist, -isten, -ismus, -morph, -phyll, -kurs,
        -vikt, -itis  (Fremdwort-/Fachwort-Endungen)
      * Grammatik-/Fachbegriffe (exact): verb, adverb, biderb, superb
      * Hardfilter: > 4 Silben ODER > 13 Zeichen
      * Eigenname-/Markenname-Heuristik: enthaelt Ziffern
      * Ausnahmen via ALLTAGS_WHITELIST (z.B. Station, Nation, Pension,
        Kind, Hand, Christ, Frist, Skikurs ...) und fuer extrem haeufige
        Woerter (haeufigkeit <= HF_MORPHO_FREI).
"""

import json
import os
import re
import sys
from collections import defaultdict

try:
    import ijson
except ImportError:
    sys.stderr.write(
        "FEHLER: ijson nicht installiert. Bitte ausfuehren:\n"
        "  python -m pip install ijson\n"
    )
    sys.exit(1)


# ── Pfade ────────────────────────────────────────────────────────────
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INPUT = os.path.join(ROOT, "output", "sprachnudel_export.v12.json")
OUT_DERB = os.path.join(ROOT, "output", "reimgruppen_derb.jsonl")
OUT_SEED = os.path.join(ROOT, "output", "seed_woerter_v22.json")

# ── Konfiguration ────────────────────────────────────────────────────
MAX_HAEUFIGKEIT = 30          # alltagsnah (KLEINER = haeufiger)
MIN_PARTNER = 4               # Mindestzahl Partner pro Gruppe nach Merge
SILBEN_MIN, SILBEN_MAX = 1, 4
MIN_KLANG_LEN = 3            # Klang muss >= 3 Zeichen haben, sonst ist die
                              # Reimgruppe trivial (z.B. "e","en","er" matchen
                              # fast jedes Wort und sind fuer Sprueche wertlos).

# Wortart-Whitelist (normalisierte Formen)
WORTART_WHITELIST = {"substantiv", "verb", "adjektiv"}

# Stopwoerter: Pronomen, Praepositionen, Artikel, Konjunktionen, Hilfsverben.
# Ein Wort (Suchwort ODER Reimpartner) wird verworfen, wenn seine
# normalisierte Form (suchwort_norm.lower() bzw. wort.lower()) hierin steht.
STOPWORTS = {
    # Pronomen
    "ich", "du", "er", "sie", "es", "wir", "ihr", "mir", "dir", "mich", "dich", "uns",
    "euch", "ihm", "ihn", "ihnen", "wer", "was", "man", "sich", "wem", "wen",
    # Praepositionen
    "bei", "von", "zu", "an", "auf", "in", "im", "am", "um", "fuer", "mit", "nach",
    "vor", "aus", "ueber", "unter", "durch", "gegen", "ohne", "bis", "seit", "ab",
    # Artikel + Konjunktionen
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "und", "oder",
    "aber", "denn", "weil", "dass", "wenn", "als", "wie", "doch", "sondern",
    # Hilfsverben / haeufige Funktionsformen
    "ist", "sind", "war", "hat", "habe", "wird", "kann", "muss", "soll", "bin", "sei",
}


def is_stopwort(wort):
    """True, wenn das Wort (lowercased) in STOPWORTS steht."""
    if not wort:
        return False
    return wort.strip().lower() in STOPWORTS


# ── Fremdwort-/Exotik-Filter (siehe AUFGABE) ────────────────────────
# Suffix-Blacklist: Woerter mit diesen Endungen gelten als Fremdwort/
# Fachbegriff und werden gefiltert (sofern sie nicht in ALLTAGS_WHITELIST
# stehen oder extrem haeufig sind).
FREMDWORT_SUFFIXE = {
    "ist", "isten", "ismus",    # Berufe / Ideologien: Theist, Journalist, Separatist
    "morph", "phyll",            # wissenschaftlich: amorph, polymorph, chlorophyll
    "kurs", "vikt",              # Fachbegriffe: Sukkurs, Diskurs, Konvikt
    "itis",                      # medizinisch: Gastritis, Appendizitis
}

# Grammatik-/Fachbegriffe (exact match, lowercased).
GRAMMATIK_BLACKLIST = {"verb", "adverb", "biderb", "superb"}

# Hartfilter: Woerter ueber dieser Laenge (Zeichen ODER Silben) gelten als
# exotisch. Silben-Schwelle orientiert sich an SILBEN_MAX (>4 Silben = raus).
MAX_ZEICHEN = 13

# Schwellwert fuer "sehr haeufig": Woerter mit haeufigkeit <= diesem Wert
# werden vom morphologischen Filter ausgenommen (sie sind ohnehin alltaeglich).
HF_MORPHO_FREI = 3

# Whitelist: Alltagswoerter, die ein Suffix aus FREMDWORT_SUFFIXE tragen,
# aber NICHT gefiltert werden sollen. Kleine, bewusste Ausnahmeliste.
ALLTAGS_WHITELIST = {
    # user-Vorgaben (per Vorgabe nicht wegwerfen)
    "station", "nation", "pension", "kind", "hand",
    # -ist: haeufige deutsche Woerter, keine Fremdwoerter
    "christ", "frist", "list", "mist", "tourist", "pianist",
    # -kurs: alltaugliche Komposita (Bildung/Sport)
    "skikurs", "tanzkurs", "sprachkurs", "yogakurs", "kochkurs", "crashkurs",
    "crash-kurs", "schnupperkurs", "fortbildungskurs", "einsteigerkurs",
    # -ismus: haeufige Abstrakta im Alltag
    "tourismus", "organismus",
}


def _enthaelt_ziffer(wort):
    """True wenn das Wort mindestens eine Ziffer enthaelt (Eigenname/Marke/Code)."""
    return bool(wort) and bool(re.search(r"\d", wort))


def ist_fremdwort_oder_exotisch(wort, silben=None, haeufigkeit=None):
    """True, wenn das Wort als Fremdwort/Exotikum/Fachbegriff gefiltert wird.

    Filter-Kriterien (siehe AUFGABE):
      - Hardfilter: > MAX_ZEICHEN Zeichen ODER > SILBEN_MAX Silben
      - Wort in GRAMMATIK_BLACKLIST (exact)
      - Fremdwort-Suffix aus FREMDWORT_SUFFIXE
      - Eigenname-/Markenname-Heuristik: enthaelt Ziffer(n)

    Ausnahmen (werden NIE gefiltert):
      - Wort in ALLTAGS_WHITELIST
      - haeufigkeit <= HF_MORPHO_FREI (sehr haeufig, gilt als alltagstauglich)
    """
    if not wort:
        return False
    w = wort.strip().lower()
    if not w:
        return False

    # Ausnahme 1: explizite Whitelist
    if w in ALLTAGS_WHITELIST:
        return False

    # Ausnahme 2: sehr haeufige Woerter vom morphologischen Filter ausnehmen
    if haeufigkeit is not None and haeufigkeit <= HF_MORPHO_FREI:
        return False

    # Eigenname-/Markenname-Heuristik: Ziffern im Wort
    if _enthaelt_ziffer(w):
        return True

    # Hardfilter: > MAX_ZEICHEN Zeichen
    if len(w) > MAX_ZEICHEN:
        return True

    # Hardfilter: > SILBEN_MAX Silben
    if silben is not None and silben > SILBEN_MAX:
        return True

    # Grammatik-/Fachbegriffe (exact match)
    if w in GRAMMATIK_BLACKLIST:
        return True

    # Fremdwort-Suffixe
    for sfx in FREMDWORT_SUFFIXE:
        if w.endswith(sfx):
            return True

    return False


# ── Wortart-Normalisierung ───────────────────────────────────────────
def norm_wortart(raw):
    """sprachnudel liefert Plural/Kleinschreibung ('substantive', 'suffix').
    Liefert eine der Whitelist-Formen oder '' wenn nicht akzeptiert."""
    if not raw:
        return ""
    r = str(raw).strip().lower()
    # direkt matchen
    if r in WORTART_WHITELIST:
        return r
    # Plural → Singular
    plural_map = {
        "substantive": "substantiv",
        "substantiven": "substantiv",
        "substantive(plural)": "substantiv",
        "verben": "verb",
        "verben(plural)": "verb",
        "adjektive": "adjektiv",
        "adjektiven": "adjektiv",
        "adjektive(plural)": "adjektiv",
    }
    if r in plural_map:
        return plural_map[r]
    return ""


# ── Filter-Helfer ────────────────────────────────────────────────────
def _int(v, default=None):
    """sicheres int-Parsing; None/default bei Fehler."""
    if v is None:
        return default
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


def _float(v, default=None):
    if v is None:
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def passes_wort_filter(w):
    """True wenn Wort den WORT-FILTER erfuellt."""
    if not w.get("hat_reime"):
        return False
    if _int(w.get("reim_count"), 0) < 4:
        return False
    hf = _int(w.get("haeufigkeit"))
    if hf is None or hf > MAX_HAEUFIGKEIT:
        return False
    silb = _int(w.get("suchwort_silben"))
    if silb is None or not (SILBEN_MIN <= silb <= SILBEN_MAX):
        return False
    wa = norm_wortart(w.get("wortart"))
    if wa not in WORTART_WHITELIST:
        return False
    # STOPWORT-Filter: suchwort_norm (Fallback: suchwort) lowercased pruefen
    norm = (w.get("suchwort_norm") or w.get("suchwort") or "").lower()
    if is_stopwort(norm):
        return False
    # FREMDWORT-/EXOTIK-FILTER (siehe AUFGABE)
    if ist_fremdwort_oder_exotisch(norm, silb, hf):
        return False
    return True


def passes_partner_filter(r):
    """True wenn ein rhymes-Eintrag den Partner-Filter erfuellt."""
    hf = _int(r.get("haeufigkeit"))
    if hf is None or hf > MAX_HAEUFIGKEIT:
        return False
    silb = _int(r.get("silben"))
    if silb is None or not (SILBEN_MIN <= silb <= SILBEN_MAX):
        return False
    wa = norm_wortart(r.get("wortart"))
    if wa not in WORTART_WHITELIST:
        return False
    # STOPWORT-Filter: Partnerwort lowercased pruefen
    pw = (r.get("wort") or "").strip()
    if is_stopwort(pw):
        return False
    # FREMDWORT-/EXOTIK-FILTER (siehe AUFGABE)
    if ist_fremdwort_oder_exotisch(pw, silb, hf):
        return False
    return True


# ── Phase 1: Stream + Sammeln ───────────────────────────────────────
def stream_and_collect():
    """Streamt die v12.json einmalig und sammelt pro klang:
      - klang_members[klang] = {wort: meta_dict}  (Suchwort + Partner)
      - word_meta[wort] = vollstaendige Metadaten (für Suchwort mit ipa/semantik)

    meta_dict-Felder: silben, haeufigkeit, wortart, semantik_score,
                      semantik_gruende, ipa, wortart_norm
    """
    klang_members = defaultdict(dict)   # klang -> {wort: meta}
    stats = {
        "total_words": 0,
        "passed_wort_filter": 0,
        "rejected_hat_reime": 0,
        "rejected_reim_count": 0,
        "rejected_haeufigkeit": 0,
        "rejected_silben": 0,
        "rejected_wortart": 0,
        "rejected_stopwort": 0,
        "rejected_fremdwort_seed": 0,
        "partner_total": 0,
        "partner_kept": 0,
        "partner_stopwort": 0,
        "partner_fremdwort": 0,
    }

    if not os.path.exists(INPUT):
        sys.stderr.write("FEHLER: Eingabedatei nicht gefunden: " + INPUT + "\n")
        sys.exit(1)

    fsize_mb = os.path.getsize(INPUT) / (1024 * 1024)
    sys.stderr.write(
        "Lese " + os.path.basename(INPUT) + " (" + str(round(fsize_mb, 1)) + " MB) ...\n"
    )

    with open(INPUT, "rb") as fh:
        for w in ijson.items(fh, "words.item"):
            stats["total_words"] += 1

            # Detail-Statistik (vor Filter)
            if not w.get("hat_reime"):
                if stats["total_words"] % 5000 == 0:
                    pass  # nur zur Verlaufs-Anzeige weiter unten
            if not w.get("hat_reime"):
                continue

            # WORT-FILTER
            if not passes_wort_filter(w):
                # reject-grund zaehlen
                if _int(w.get("reim_count"), 0) < 4:
                    stats["rejected_reim_count"] += 1
                    continue
                hf = _int(w.get("haeufigkeit"))
                if hf is None or hf > MAX_HAEUFIGKEIT:
                    stats["rejected_haeufigkeit"] += 1
                    continue
                silb = _int(w.get("suchwort_silben"))
                if silb is None or not (SILBEN_MIN <= silb <= SILBEN_MAX):
                    stats["rejected_silben"] += 1
                    continue
                if norm_wortart(w.get("wortart")) not in WORTART_WHITELIST:
                    stats["rejected_wortart"] += 1
                    continue
                # letzter Check: Stopwort
                norm = (w.get("suchwort_norm") or w.get("suchwort") or "").lower()
                if is_stopwort(norm):
                    stats["rejected_stopwort"] += 1
                    continue
                # FREMDWORT-/EXOTIK-Filter (neu)
                if ist_fremdwort_oder_exotisch(norm, silb, hf):
                    stats["rejected_fremdwort_seed"] += 1
                    continue
                continue

            stats["passed_wort_filter"] += 1
            klang = w.get("klang") or ""
            if not klang:
                continue
            sw = (w.get("suchwort") or "").strip()
            if not sw:
                continue

            # Suchwort als Member eintragen (mit vollen Metadaten)
            wa_norm = norm_wortart(w.get("wortart"))
            sem_score = _float(w.get("semantik_score"))
            ipa = w.get("ipa")
            if not isinstance(ipa, list):
                ipa = ipa if ipa else []

            suchwort_meta = {
                "wort": sw,
                "silben": _int(w.get("suchwort_silben"), 0),
                "haeufigkeit": _int(w.get("haeufigkeit"), MAX_HAEUFIGKEIT),
                "wortart": wa_norm,
                "semantik_score": sem_score if sem_score is not None else 0.0,
                "semantik_gruende": w.get("semantik_gruende") or [],
                "ipa": ipa,
                "_is_seed_candidate": True,
            }
            # Merge: nur updaten wenn Meta noch fehlt ODER neues Meta vollstaendiger
            existing = klang_members[klang].get(sw.lower())
            if existing is None:
                klang_members[klang][sw.lower()] = suchwort_meta
            else:
                # behalte reicheres Meta (mit ipa/semantik)
                if (sem_score or ipa) and not (
                    existing.get("ipa") or existing.get("semantik_score")
                ):
                    klang_members[klang][sw.lower()] = suchwort_meta

            # Partner durchgehen (rhymes[])
            rhymes = w.get("rhymes") or []
            for r in rhymes:
                stats["partner_total"] += 1
                pw = (r.get("wort") or "").strip()
                if not pw:
                    continue
                if is_stopwort(pw):
                    stats["partner_stopwort"] += 1
                    continue
                # FREMDWORT-/EXOTIK-Filter auf Partner-Ebene (neu)
                p_silb = _int(r.get("silben"))
                p_hf = _int(r.get("haeufigkeit"))
                if ist_fremdwort_oder_exotisch(pw, p_silb, p_hf):
                    stats["partner_fremdwort"] += 1
                    continue
                if not passes_partner_filter(r):
                    continue
                stats["partner_kept"] += 1
                pmeta = {
                    "wort": pw,
                    "silben": _int(r.get("silben"), 0),
                    "haeufigkeit": _int(r.get("haeufigkeit"), MAX_HAEUFIGKEIT),
                    "wortart": norm_wortart(r.get("wortart")),
                    "semantik_score": 0.0,
                    "semantik_gruende": [],
                    "ipa": [],
                    "_is_seed_candidate": False,
                }
                key = pw.lower()
                # Nur eintragen wenn noch nicht vorhanden (Suchwort hat Vorrang)
                if key not in klang_members[klang]:
                    klang_members[klang][key] = pmeta

            if stats["passed_wort_filter"] % 1000 == 0:
                sys.stderr.write(
                    "  " + str(stats["passed_wort_filter"])
                    + " Worteingaben verarbeitet, "
                    + str(len(klang_members)) + " klaenge aktiv\r"
                )

    sys.stderr.write("\n")
    return klang_members, stats


# ── Phase 2: Gruppen bauen + schreiben ───────────────────────────────
def build_and_write(klang_members):
    """Erzeugt pro klang eine Gruppe (falls >= MIN_PARTNER Mitglieder)
    und schreibt beide Output-Dateien."""

    gruppen = []
    silben_dist = defaultdict(int)
    ge6_partner = 0
    discarded = 0

    for klang, members in klang_members.items():
        if not klang or not members:
            continue
        # Triviale Klaenge ausschliessen (z.B. "e", "en", "er", "in")
        if len(klang) < MIN_KLANG_LEN:
            continue
        # members ist dict {wort_lower: meta}. Anzahl eindeutiger Woerter.
        n = len(members)
        if n < MIN_PARTNER:
            discarded += 1
            continue

        # seed = haeufigstes Wort (kleinste haeufigkeit)
        # bevorzuge seed_candidates
        items = list(members.values())
        # sortiere nach haeufigkeit asc; bei Gleichstand seed_candidate zuerst
        items.sort(key=lambda m: (m["haeufigkeit"], 0 if m.get("_is_seed_candidate") else 1, m["wort"]))
        seed_meta = items[0]
        seed_wort = seed_meta["wort"]
        seed_silben = seed_meta["silben"]

        silben_dist[seed_silben] += 1

        # woerter = alle AUSSER seed, sortiert: semantik_score desc, dann haeufigkeit asc
        rest = [m for m in items if m["wort"].lower() != seed_wort.lower()]
        rest.sort(key=lambda m: (-(m.get("semantik_score") or 0), m["haeufigkeit"], m["wort"]))

        if len(rest) >= 6:
            ge6_partner += 1

        woerter_out = []
        for m in rest:
            woerter_out.append({
                "wort": m["wort"],
                "silben": m["silben"],
                "haeufigkeit": m["haeufigkeit"],
                "semantik_score": m.get("semantik_score") or 0.0,
                "semantik_gruende": m.get("semantik_gruende") or [],
                "ipa": m.get("ipa") or [],
            })

        partner_set = sorted({m["wort"] for m in items})

        gruppe = {
            "klang": klang,
            "seed": seed_wort,
            "woerter": woerter_out,
            "partner_set": partner_set,
        }
        gruppen.append(gruppe)

    # sortiere gruppen nach seed haeufigkeit (haeufigstes zuerst)
    # wir brauchen seed-meta dazu — neu sortieren nach haeufigkeit
    seed_hf = {}
    for g in gruppen:
        # haeufigkeit des seeds = min haeufigkeit in woerter (da seed aus sortierten items[0])
        # wir speichern nicht die seed-haeufigkeit direkt — wir rekonstruieren
        # Einfacher: wir sortieren gruppen alphabetisch nach klang fuer stabilitaet
        pass
    gruppen.sort(key=lambda g: g["klang"])

    # Output 1: reimgruppen_derb.jsonl
    with open(OUT_DERB, "w", encoding="utf-8") as f:
        for g in gruppen:
            f.write(json.dumps(g, ensure_ascii=False) + "\n")

    # Output 2: seed_woerter_v22.json
    # Liste der seed-Woerter aller Gruppen, sortiert nach Haeufigkeit aufsteigend.
    # Da wir die seed-haeufigkeit nicht direkt gespeichert haben, muessen wir sie
    # nochmal aus den members holen. Einfach: wir merken sie beim Build.
    # Korrektur: wir bauen seed_liste direkt hier.
    seed_liste = []
    # Wir iteriere gruppen und finde seed-haeufigkeit aus klang_members
    for g in gruppen:
        klang = g["klang"]
        members = klang_members.get(klang, {})
        seed_key = g["seed"].lower()
        seed_meta = members.get(seed_key)
        hf = seed_meta["haeufigkeit"] if seed_meta else MAX_HAEUFIGKEIT
        silb = seed_meta["silben"] if seed_meta else 0
        seed_liste.append({
            "wort": g["seed"],
            "haeufigkeit": hf,
            "silben": silb,
            "klang": klang,
        })
    seed_liste.sort(key=lambda s: (s["haeufigkeit"], s["wort"]))

    # Dedup: gleicher seed-Wert nur einmal (kleinste Haeufigkeit wurde durch
    # Sortierung zuerst platziert). Behalte den ersten Treffer pro wort_lower.
    seen_seeds = set()
    seed_dedup = []
    for s in seed_liste:
        key = s["wort"].lower()
        if key in seen_seeds:
            continue
        seen_seeds.add(key)
        seed_dedup.append(s)
    seed_liste = seed_dedup

    with open(OUT_SEED, "w", encoding="utf-8") as f:
        json.dump(
            {
                "version": "v22",
                "beschreibung": "Seed-Liste aus build_reimgruppen.py (v12-Quelle)",
                "anzahl": len(seed_liste),
                "sortierung": "haeufigkeit aufsteigend (1 = am haeufigsten)",
                "seeds": [s["wort"] for s in seed_liste],
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    return gruppen, silben_dist, ge6_partner, discarded, seed_liste


# ── Abschluss-Report ─────────────────────────────────────────────────
def print_report(gruppen, silben_dist, ge6_partner, discarded, seed_liste, stats):
    print("")
    print("=" * 60)
    print("BUILD-REPORT build_reimgruppen.py")
    print("=" * 60)

    print("\n[1] STREAM-STATISTIK")
    print("  Gelesene Wort-Eintraege : " + str(stats["total_words"]))
    print("  Wort-Filter bestanden   : " + str(stats["passed_wort_filter"]))
    print("  Reject reim_count < 4   : " + str(stats["rejected_reim_count"]))
    print("  Reject haeufigkeit > 30 : " + str(stats["rejected_haeufigkeit"]))
    print("  Reject silben           : " + str(stats["rejected_silben"]))
    print("  Reject wortart          : " + str(stats["rejected_wortart"]))
    print("  Reject STOPWORT (seed)  : " + str(stats["rejected_stopwort"]))
    print("  Reject FREMDWORT (seed) : " + str(stats["rejected_fremdwort_seed"]))
    print("  Partner gesamt          : " + str(stats["partner_total"]))
    print("  Partner behalten        : " + str(stats["partner_kept"]))
    print("  Partner STOPWORT        : " + str(stats["partner_stopwort"]))
    print("  Partner FREMDWORT       : " + str(stats["partner_fremdwort"]))
    print("  --- FREMDWORT-FILTER gesamt entfernt: "
          + str(stats["rejected_fremdwort_seed"] + stats["partner_fremdwort"])
          + " Woerter (Seed + Partner) ---")

    print("\n[2] GRUPPEN")
    print("  Gebaute Gruppen         : " + str(len(gruppen)))
    print("  Verworfen (< " + str(MIN_PARTNER) + " Partner) : " + str(discarded))
    print("  Gruppen mit >= 6 Partner: " + str(ge6_partner))

    print("\n[3] SILBEN-VERTEILUNG DER SEEDS")
    for silb in sorted(silben_dist.keys()):
        bar = "#" * min(silben_dist[silb] // 10, 40)
        print("  " + str(silb) + " Silben: " + str(silben_dist[silb]).rjust(5)
              + "  " + bar)

    print("\n[4] 10 BEISPIELGRUPPEN")
    shown = 0
    for g in gruppen:
        if shown >= 10:
            break
        worte = [w["wort"] for w in g["woerter"][:6]]
        print("  klang=" + g["klang"] + "  seed=" + g["seed"]
              + "  (" + str(len(g["woerter"])) + " Partner)"
              + "\n      erste 6: " + ", ".join(worte))
        shown += 1

    print("\n[5] TOP 15 SEEDS (nach Haeufigkeit)")
    for s in seed_liste[:15]:
        print("  " + s["wort"].ljust(18)
              + " haeufigkeit=" + str(s["haeufigkeit"]).rjust(3)
              + "  silben=" + str(s["silben"])
              + "  klang=" + s["klang"])

    print("\n[6] OUTPUT-DATEIEN")
    print("  " + OUT_DERB + "  (" + str(os.path.getsize(OUT_DERB)) + " Bytes)")
    print("  " + OUT_SEED + "  (" + str(os.path.getsize(OUT_SEED)) + " Bytes)")

    print("\n" + "=" * 60)
    print("FERTIG.")
    print("=" * 60)


# ── main ─────────────────────────────────────────────────────────────
def main():
    sys.stderr.write("build_reimgruppen.py startet.\n")
    klang_members, stats = stream_and_collect()
    sys.stderr.write(
        "Phase 1 fertig: " + str(len(klang_members)) + " klaenge gesammelt.\n"
    )
    gruppen, silben_dist, ge6, discarded, seed_liste = build_and_write(klang_members)
    sys.stderr.write(
        "Phase 2 fertig: " + str(len(gruppen)) + " Gruppen geschrieben.\n"
    )
    print_report(gruppen, silben_dist, ge6, discarded, seed_liste, stats)


if __name__ == "__main__":
    main()
