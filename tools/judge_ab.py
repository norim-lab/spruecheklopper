"""tools/judge_ab.py — Reproduzierbarer A/B-Harness fuer den bge-m3-Embedding-Bonus (P).

Misst den Effekt des Embedding-Bonus auf Judge-Score und Reimwort-Wahl.
Beide Gruppen (A=Embedding AUS, B=Embedding AN) werden mit demselben
Harness, denselben Seeds und identischen Settings gemessen.

Vorgehen (paarweise):
    fuer jeden der 6 fixierten thema-Seeds:
        1× generate_spruch_best(thema=seed, use_embeddings=False, ...)  # Gruppe A
        1× generate_spruch_best(thema=seed, use_embeddings=True,  ...)  # Gruppe B
        B: zusaetzlich bge-m3 cosine similarity seed<->reimwort erfassen

Ausgabe:
    - Konsole: Live-Log + finale Tabelle + Avg-Vergleich + Empfehlung
    - output/judge_ab_report.json: vollstaendige Resultate fuer spaetere Analyse

Voraussetzungen:
    - config.json mit deepinfra_api_key
    - output/embedding_cache.json (vorwaermen via tools/build_embedding_cache.py)
    - generate_spruch_best + _cosine_similarity aus spruch_app.generator

Hinweis:
    Da der Generator intern ``random.Random()`` ohne Seed nutzt und LLM-Aufrufe
    nicht-deterministisch sind, ist der reine Wortwahl-Diff nicht allein dem
    Embedding-Bonus zuzuschreiben; er enthaelt Rauschen. Die Avg-Vergleiche
    ueber 6 Paare liefern dennoch einen belastbaren Schwaert.
"""

import json
import os
import sys
import time
from pathlib import Path

# Pfad-Setup: Projekt-Root in sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spruch_app import generator as gen

# ── Fixe Konfiguration ─────────────────────────────────────────────────────

# 6 fixierte thema-Seeds. Bauer ist verpflichtend (war im alten Lauf #1).
# Rest: alltagsnahe Themen, die im Bauernspruch-Kontext passen.
SEEDS = [
    "Bauer",     # verpflichtend (war alter Lauf #1)
    "Liebe",     # alltagsnah, viele Assoziationen
    "Geld",      # kritisch-sozialer Ton, gut fuer Bauernspruch
    "Tod",       # derbe Themenvielfalt
    "Kind",      # familiär-alltagsnah
    "Frau",      # Bauernspruch-typisch
]

# fmt pro Seed (gleich in A und B). Abwechselnd fuer Streuung.
FMTS = [
    "gemischt",  # Bauer
    "AABB-4",    # Liebe
    "AA-2",      # Geld
    "gemischt",  # Tod
    "AABB-4",    # Kind
    "AA-2",      # Frau
]

# Identische Settings fuer beide Gruppen
CANDIDATES = 8
MIN_SCORE  = 4
DERBHEIT   = "derb"
REIM_STRENGE = "DB-streng"
MODEL      = "grok-4.3"

# Embedding-Config (mirror aus generator.py, nur zur Dokumentation)
EMBEDDING_BONUS_THRESHOLD = gen.EMBEDDING_BONUS_THRESHOLD  # 0.60
EMBEDDING_BONUS           = gen.EMBEDDING_BONUS            # 0.25

# Pfade
REPORT_PATH = ROOT / "output" / "judge_ab_report.json"


# ── Hilfsfunktionen ────────────────────────────────────────────────────────

def reset_gen_state():
    """Setzt den Generator-Status zurueck (vor jedem Run)."""
    gen._status_reset()


def extract_reimwoerter(result):
    """Extrahiert die gesampelten Reimwoerter aus dem generate_spruch_best-Result.

    Der Generator schreibt 'reimwoerter' (Liste von Strings) ins Ergebnis.
    Fallback auf klang_gruppen-Informationen, falls Reimwoerter leer.
    """
    rw = result.get("reimwoerter") or []
    if rw:
        return [str(w).lower() for w in rw]
    # Fallback: Cast/Reim-Infos aus den Klanggruppen
    cast = result.get("cast") or []
    return [str(c).lower() for c in cast if c]


def compute_embedding_sims(seed, woerter, cache):
    """Berechnet cosine sim(seed, wort) aus dem Embedding-Cache.

    Gibt dict {wort_lower: sim_or_None} zurueck. None, wenn eines der
    Woerter nicht im Cache steht oder der seed fehlt.
    """
    if not cache:
        return {w: None for w in woerter}
    seed_lower = (seed or "").lower()
    seed_vec = cache.get(seed_lower)
    if seed_vec is None:
        return {w: None for w in woerter}
    sims = {}
    for w in woerter:
        vec = cache.get(w.lower())
        if vec is None:
            sims[w] = None
            continue
        try:
            sim = gen._cosine_similarity(seed_vec, vec)
            sims[w] = round(max(0.0, min(1.0, sim)), 4)
        except Exception:
            sims[w] = None
    return sims


def load_embedding_cache():
    """Laedt output/embedding_cache.json (oder None bei Fehler)."""
    p = gen.EMBEDDING_CACHE_PATH
    if not p.exists():
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        sys.stderr.write("WARN: Embedding-Cache nicht lesbar: " + str(e) + "\n")
        return None


def run_one(label, seed, fmt, use_embeddings):
    """Fuehrt EINE Generierung durch. Gibt Resultat-Dict zurueck.

    label: 'A' oder 'B' (nur fuer Logging)
    """
    print("\n" + "=" * 72)
    print("  Gruppe " + label + " | seed='" + seed + "' | fmt=" + fmt
          + " | use_embeddings=" + str(use_embeddings))
    print("=" * 72)

    reset_gen_state()
    t0 = time.time()
    try:
        result = gen.generate_spruch_best(
            mode=("short" if fmt == "AA-2" else "long"),
            candidates=CANDIDATES,
            min_score=MIN_SCORE,
            model=MODEL,
            thema=seed,
            derbheit=DERBHEIT,
            reim_strenge=REIM_STRENGE,
            fmt_request=fmt,
            use_embeddings=use_embeddings,
        )
    except Exception as e:
        print("  FEHLER waehrend Generierung: " + str(e))
        return {
            "label": label, "seed": seed, "fmt": fmt,
            "use_embeddings": use_embeddings,
            "ok": False, "error": str(e),
            "judge_score": None, "reimwoerter": [], "spruch": "",
            "elapsed_s": round(time.time() - t0, 1),
        }
    elapsed = time.time() - t0

    ok = bool(result.get("ok", False))
    judge_score = result.get("judge_score")
    try:
        judge_score = float(judge_score) if judge_score is not None else None
    except (TypeError, ValueError):
        judge_score = None
    reimwoerter = extract_reimwoerter(result)
    spruch = (result.get("spruch") or result.get("letzter_spruch") or "").strip()
    klang = result.get("klang_gruppen") or []
    err = result.get("error")

    print("  ok=" + str(ok) + " | judge_score=" + str(judge_score)
          + " | reimwoerter=" + str(len(reimwoerter))
          + " | klang=" + str(klang[:2])
          + " | dauer=" + str(round(elapsed, 1)) + "s")
    if spruch:
        print("  Spruch: " + spruch[:200].replace("\n", " | "))

    return {
        "label": label, "seed": seed, "fmt": fmt,
        "use_embeddings": use_embeddings,
        "ok": ok, "error": err,
        "judge_score": judge_score,
        "reimwoerter": reimwoerter,
        "klang_gruppen": klang,
        "spruch": spruch,
        "elapsed_s": round(elapsed, 1),
        "self_score": result.get("self_score"),
        "reject_reasons": result.get("reject_reasons", []),
        "ipa_checks": result.get("ipa_checks"),
        "db_checks": result.get("db_checks"),
    }


def main():
    print("#" * 72)
    print("# judge_ab.py — A/B-Harness fuer bge-m3-Embedding-Bonus (P)")
    print("#")
    print("# Seeds      : " + ", ".join(SEEDS))
    print("# fmts       : " + ", ".join(FMTS))
    print("# candidates : " + str(CANDIDATES))
    print("# min_score  : " + str(MIN_SCORE))
    print("# derbheit   : " + DERBHEIT + " | reim_strenge: " + REIM_STRENGE)
    print("# model      : " + MODEL)
    print("# embedding  : threshold=" + str(EMBEDDING_BONUS_THRESHOLD)
          + " bonus=" + str(EMBEDDING_BONUS))
    print("#" * 72)

    # Embedding-Cache pruefen (Voraussetzung fuer Gruppe B)
    cache = load_embedding_cache()
    if cache is None:
        print("ABBRUCH: output/embedding_cache.json fehlt. "
              "Zuerst 'python tools/build_embedding_cache.py' ausfuehren.")
        sys.exit(1)
    print("Embedding-Cache: " + str(len(cache)) + " Woerter geladen")
    print()

    # API-Key pruefen (Voraussetzung fuer LLM-Aufrufe)
    api_key = gen._read_api_key()
    if not api_key:
        print("ABBRUCH: Kein API-Key in config.json gefunden.")
        sys.exit(1)
    print("API-Key gefunden.")

    # ── Hauptloop: paarweise A/B ──
    paare = []
    overall_t0 = time.time()

    for i, seed in enumerate(SEEDS):
        fmt = FMTS[i]
        print("\n\n" + "#" * 72)
        print("# SEED " + str(i + 1) + "/" + str(len(SEEDS)) + ": '"
              + seed + "' (fmt=" + fmt + ")")
        print("#" * 72)

        # Gruppe A: Embedding AUS (Baseline)
        res_a = run_one("A", seed, fmt, use_embeddings=False)

        # Gruppe B: Embedding AN
        res_b = run_one("B", seed, fmt, use_embeddings=True)

        # Embedding-Sims fuer Gruppe B berechnen
        sims_b = compute_embedding_sims(seed, res_b["reimwoerter"], cache)
        res_b["embedding_sims"] = sims_b
        # Bonused Worte (sim >= threshold)
        res_b["bonused_woerter"] = sorted(
            [w for w, s in sims_b.items() if s is not None and s >= EMBEDDING_BONUS_THRESHOLD]
        )

        # Wortwahl-Diff A vs B
        set_a = set(res_a["reimwoerter"])
        set_b = set(res_b["reimwoerter"])
        diff = {
            "only_in_A": sorted(set_a - set_b),
            "only_in_B": sorted(set_b - set_a),
            "common":    sorted(set_a & set_b),
        }
        # Hat Bonus die Wortwahl geaendert? (wenigstens ein Wort anders)
        wortwahl_geaendert = bool(diff["only_in_A"] or diff["only_in_B"])

        paare.append({
            "seed": seed,
            "fmt": fmt,
            "A": res_a,
            "B": res_b,
            "reimwort_diff": diff,
            "wortwahl_geaendert": wortwahl_geaendert,
        })

    overall_elapsed = time.time() - overall_t0

    # ── Auswertung ──
    print("\n\n" + "=" * 72)
    print("  AUSWERTUNG")
    print("=" * 72)

    # Tabelle
    print("\nSeed      | fmt     | Judge A | Judge B | Delta | Worte A->B diff             | Spruch B (gekuerzt)")
    print("-" * 120)
    sum_a = 0.0
    sum_b = 0.0
    n_valid_a = 0
    n_valid_b = 0
    n_wortwahl_geaendert = 0
    for p in paare:
        ja = p["A"]["judge_score"]
        jb = p["B"]["judge_score"]
        if ja is not None:
            sum_a += ja
            n_valid_a += 1
        if jb is not None:
            sum_b += jb
            n_valid_b += 1
        delta = "  -  "
        if ja is not None and jb is not None:
            delta = "{:+.2f}".format(jb - ja)
        if p["wortwahl_geaendert"]:
            n_wortwahl_geaendert += 1
        diff_summary = ("nur A: " + ",".join(p["reimwort_diff"]["only_in_A"][:3])
                        + " | nur B: " + ",".join(p["reimwort_diff"]["only_in_B"][:3]))
        spruch_b = (p["B"]["spruch"] or "")[:50].replace("\n", " | ")
        ja_str = "{:.2f}".format(ja) if ja is not None else " - "
        jb_str = "{:.2f}".format(jb) if jb is not None else " - "
        print("{:9s} | {:7s} | {:7s} | {:7s} | {:5s} | {:28s} | {}".format(
            p["seed"], p["fmt"], ja_str, jb_str, delta, diff_summary[:28], spruch_b))

    avg_a = (sum_a / n_valid_a) if n_valid_a else 0.0
    avg_b = (sum_b / n_valid_b) if n_valid_b else 0.0
    delta_avg = (avg_b - avg_a) if (n_valid_a and n_valid_b) else 0.0

    print("\nAvg Judge A (Embedding AUS): {:.2f}  (n={})".format(avg_a, n_valid_a))
    print("Avg Judge B (Embedding AN):  {:.2f}  (n={})".format(avg_b, n_valid_b))
    print("Gesamt-Delta (B - A):        {:+.2f}".format(delta_avg))
    print("\nWortwahl durch Bonus geaendert: "
          + str(n_wortwahl_geaendert) + "/" + str(len(paare)) + " Saetzen")

    # Embedding-Kosten: BGE-M3 ≈ $0.01 / 1M tokens (Standardvertrag DeepInfra)
    # 6350 Worte im Cache, jeder ~1 token. Cache wurde 1x gebaut, hier nur
    # lookup (kein API-Call pro Generierung).
    cache_tokens = len(cache)  # grob 1 token pro wort
    cache_cost_usd = (cache_tokens / 1_000_000.0) * 0.01
    print("\nKosten-Check (BGE-M3 ≈ $0.01/1M tokens):")
    print("  Cache-Vollstaendig: " + str(cache_tokens)
          + " Worte ≈ " + str(cache_tokens) + " tokens")
    print("  Cache-Baukosten (einmalig): ≈ $" + "{:.6f}".format(cache_cost_usd))
    print("  Laufende Kosten pro A/B-Lauf: $0 (Cache-Hits)")

    # Empfehlung
    print("\n" + "-" * 72)
    print("EMPFEHLUNG")
    print("-" * 72)
    if delta_avg > 0.10 and n_wortwahl_geaendert >= 3:
        print("Embedding-AN hebt den Avg-Judge messbar (Delta >= +0.10) UND aendert "
              "die Wortwahl in >= 50% der Saetze. -> P scharf lassen (default AN).")
    elif delta_avg > 0.05 or n_wortwahl_geaendert >= 3:
        print("Schwacher Effekt: Delta > 0.05 ODER Wortwahl in >= 50% geaendert. "
              "-> P scharf lassen, aber Growth beobachten.")
    elif n_wortwahl_geaendert == 0:
        print("KEIN Effekt: Bonus hat in 0/6 Saetzen die Wortwahl geaendert. "
              "-> P default AUS (folgenlos, nur Cache-Baukosten).")
    else:
        print("Wortwahl in " + str(n_wortwahl_geaendert) + "/6 geaendert, "
              "aber Avg-Delta " + "{:+.2f}".format(delta_avg)
              + " nicht signifikant. -> P default AUS (kein messbarer Qualitaetsgewinn).")

    print("\nGesamt-Laufzeit: " + str(round(overall_elapsed, 1)) + "s")

    # ── Report speichern ──
    report = {
        "seeds": SEEDS,
        "fmts": FMTS,
        "settings": {
            "candidates": CANDIDATES,
            "min_score": MIN_SCORE,
            "derbheit": DERBHEIT,
            "reim_strenge": REIM_STRENGE,
            "model": MODEL,
            "embedding_bonus_threshold": EMBEDDING_BONUS_THRESHOLD,
            "embedding_bonus": EMBEDDING_BONUS,
        },
        "paare": paare,
        "auswertung": {
            "avg_a": round(avg_a, 3),
            "avg_b": round(avg_b, 3),
            "delta": round(delta_avg, 3),
            "n_valid_a": n_valid_a,
            "n_valid_b": n_valid_b,
            "n_wortwahl_geaendert": n_wortwahl_geaendert,
            "n_paare": len(paare),
            "gesamt_laufzeit_s": round(overall_elapsed, 1),
        },
        "cache_info": {
            "woerter": len(cache),
            "cache_cost_usd_estimated": round(cache_cost_usd, 6),
        },
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("\nReport gespeichert: " + str(REPORT_PATH))


if __name__ == "__main__":
    main()
