"""tools/judge_models.py — Modell-Vergleich: grok-4.3 vs. staerkere Challenger.

Frage: Produziert ein staerkeres (teureres) Generierungsmodell witzigere
Sprueche als grok-4.3? Reine Baseline-MESSUNG — generator.py wird NICHT
inhaltlich geaendert, das Modell ausschliesslich per Parameter (model=...)
variiert. Embedding bleibt AUS (P-Entscheid). Judge ist KONSTANT grok-4.3.

Vorgehen:
    fuer jedes Modell M in MODELS:
        fuer jeden der 6 fixierten thema-Seeds:
            1x generate_spruch_best(model=M, judge_model="grok-4.3",
                                    use_embeddings=False, sonst identisch)

Ausgabe:
    - Konsole: Live-Log + Tabelle + Avg-Vergleich + Kosten + Empfehlung
    - output/judge_models_report.json: vollstaendige Resultate
    - output/blind_check.md: anonyme Sprueche (OHNE Modell-Label), pro Seed
      zufaellig durchmischt als A) ... / B) ..., Aufloesung am Ende.
      (LOKAL — wird NICHT committet, menschliche Ground-Truth.)

Voraussetzungen:
    - config.json mit grok_api_key + deepinfra_api_key
    - generate_spruch_best + session_stats/session_reset aus spruch_app.generator

CAVEATS (Ergebnis ist TENDENZ, nicht Beweis):
    (1) Der Generator zieht Klanggruppen via random.Random() OHNE Seed ->
        die Modelle vergleichen NICHT identisches Reimmaterial. Das Delta
        enthaelt also Rauschen (wie beim P-Lauf).
    (2) grok-4.3 bewertet u.a. eigene Outputs -> moeglicher Selbstbias
        zugunsten von grok-4.3.
    (3) session_stats() erfasst Generator- UND Judge-Tokens zusammen. Da der
        Judge (grok-4.3) konstant ist, ist sein Token-Overhead in beiden
        Modellen ~gleich — das Delta der Totaltokens entspricht also dem
        Generator-Unterschied.
"""

import json
import os
import random as _random
import sys
import time
from pathlib import Path

# Pfad-Setup: Projekt-Root in sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spruch_app import generator as gen

# ── Fixe Konfiguration ─────────────────────────────────────────────────────

# Modell-Palette. (model_id, label). Reihenfolge =run-Reihenfolge.
# VORARBEIT-Ergebnis (live geprueft 2026-06-16):
#   grok-4.3                -> 200 OK (xAI)              — Baseline/Kontrolle
#   deepseek-ai/DeepSeek-V3.2 -> 200 OK (DeepInfra)      — Challenger
#   Qwen/Qwen3-235B...      -> 429 Rate-Limited           — optional ausgelassen
#   gemini-3.1-pro          -> n/a (Dispatcher matched nicht auf "gemini",
#                                    kein Endpunkt in _llm_call)
#   gpt-5.4                 -> n/a (kein OpenAI-Key in config.json)
MODELS = [
    ("grok-4.3", "grok-4.3"),                    # Baseline
    ("deepseek-ai/DeepSeek-V3.2", "DeepSeek-V3.2"),  # Challenger
]

# 6 fixierte thema-Seeds (identisch zu tools/judge_ab.py fuer Vergleichbarkeit)
SEEDS = [
    "Bauer",
    "Liebe",
    "Geld",
    "Tod",
    "Kind",
    "Frau",
]

# fmt pro Seed (gleich fuer alle Modelle). Abwechselnd fuer Streuung.
FMTS = [
    "gemischt",  # Bauer
    "AABB-4",    # Liebe
    "AA-2",      # Geld
    "gemischt",  # Tod
    "AABB-4",    # Kind
    "AA-2",      # Frau
]

# Identische Settings fuer alle Modelle (use_embeddings IMMER False — P-Entscheid)
CANDIDATES  = 8
MIN_SCORE   = 4
DERBHEIT    = "derb"
REIM_STRENGE = "DB-streng"
JUDGE_MODEL = "grok-4.3"   # KONSTANT fuer alle Generatoren (Vergleichbarkeit)
USE_EMBEDDINGS = False

# Kosten-Annahmen (USD pro 1M Tokens; Input/Output).
# Quelle: Hersteller-Public-Pricing Stand 2026-06. Fuer grok-4.3 per
# Spec-Vorgabe. DeepSeek-V3.2: DeepInfra-Public-Pricing.
COST_PER_1M = {
    "grok-4.3":                  {"input": 1.25, "output": 2.50},
    "deepseek-ai/DeepSeek-V3.2": {"input": 0.27, "output": 1.10},
}

# Pfade
REPORT_PATH     = ROOT / "output" / "judge_models_report.json"
BLIND_CHECK_PATH = ROOT / "output" / "blind_check.md"


# ── Hilfsfunktionen ────────────────────────────────────────────────────────

def reset_gen_state():
    """Setzt Generator-Status + Session-Token-Zaehler zurueck."""
    gen._status_reset()
    gen.session_reset()


def extract_reimwoerter(result):
    """Extrahiert die gesampelten Reimwoerter aus dem Result."""
    rw = result.get("reimwoerter") or []
    if rw:
        return [str(w).lower() for w in rw]
    cast = result.get("cast") or []
    return [str(c).lower() for c in cast if c]


def run_one(model_id, label, seed, fmt):
    """Fuehrt EINE Generierung durch. Gibt Resultat-Dict zurueck.

    model_id: Modell-ID fuer generate_spruch_best(model=...)
    label:    Anzeige-Label fuer Logging
    """
    print("\n" + "=" * 72)
    print("  Modell " + label + " | seed='" + seed + "' | fmt=" + fmt
          + " | use_embeddings=" + str(USE_EMBEDDINGS))
    print("=" * 72)

    reset_gen_state()
    t0 = time.time()
    try:
        result = gen.generate_spruch_best(
            mode=("short" if fmt == "AA-2" else "long"),
            candidates=CANDIDATES,
            min_score=MIN_SCORE,
            model=model_id,
            judge_model=JUDGE_MODEL,
            thema=seed,
            derbheit=DERBHEIT,
            reim_strenge=REIM_STRENGE,
            fmt_request=fmt,
            use_embeddings=USE_EMBEDDINGS,
        )
    except Exception as e:
        print("  FEHLER waehrend Generierung: " + str(e))
        return {
            "model_id": model_id, "label": label, "seed": seed, "fmt": fmt,
            "ok": False, "error": str(e),
            "judge_score": None, "reimwoerter": [], "spruch": "",
            "elapsed_s": round(time.time() - t0, 1),
            "tokens": {"prompt": 0, "completion": 0, "gesamt": 0},
            "kosten_usd": 0.0,
        }
    elapsed = time.time() - t0

    # Session-Tokens (gen + judge kombiniert) nach dem Run erfassen
    stats = gen.session_stats()
    tokens = stats.get("tokens", {})
    pt = int(tokens.get("prompt", 0))
    ct = int(tokens.get("completion", 0))

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
          + " | tokens(p/c)=" + str(pt) + "/" + str(ct)
          + " | dauer=" + str(round(elapsed, 1)) + "s")
    if spruch:
        print("  Spruch: " + spruch[:200].replace("\n", " | "))

    return {
        "model_id": model_id, "label": label, "seed": seed, "fmt": fmt,
        "ok": ok, "error": err,
        "judge_score": judge_score,
        "reimwoerter": reimwoerter,
        "klang_gruppen": klang,
        "spruch": spruch,
        "judge_begruendung": result.get("judge_begruendung", ""),
        "elapsed_s": round(elapsed, 1),
        "tokens": {"prompt": pt, "completion": ct, "gesamt": pt + ct},
    }


def calc_cost(model_id, pt, ct):
    """Kosten in USD aus Tokens + Preis-Tabelle."""
    p = COST_PER_1M.get(model_id, {"input": 0.0, "output": 0.0})
    return (pt * p["input"] + ct * p["output"]) / 1_000_000.0


def write_blind_check(rows_per_seed, model_labels):
    """Schreibt output/blind_check.md mit anonymen, pro Seed gemischten Spruechen.

    rows_per_seed: {seed: [ {label, spruch, judge_score, fmt}, ... ]}
    model_labels:  Liste der Modell-Labels (fuer Aufloesung).

    Pro Seed werden die Sprueche zufaellig durchgemischt und als
    A) ... / B) ... (ggf. C...) ohne Modell-Namen aufgefuehrt. Die
    Zuordnung (Buchstabe -> Modell) steht separat am Ende.
    """
    lines = []
    lines.append("# Blind-Check — Modell-Vergleich (anonym)")
    lines.append("")
    lines.append("Sprueche pro Seed zufaellig durchmischt, OHNE Modell-Label.")
    lines.append("Judge-Score bewusst ENTFERNT — reine menschliche Bewertung.")
    lines.append("Aufloesung (welcher Buchstabe = welches Modell) am Ende.")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Pro Seed mischen
    aufloesung = []  # [{"seed":..., "mapping": {"A": label, ...}}, ...]
    rng = _random.Random(12345)  # fester Seed fuer Reproduzierbarkeit der Mischung
    for seed, rows in rows_per_seed.items():
        fmt = rows[0]["fmt"] if rows else "?"
        lines.append("## Seed: " + seed + "  (fmt=" + fmt + ")")
        lines.append("")
        order = list(range(len(rows)))
        rng.shuffle(order)
        buchstaben = [chr(ord("A") + i) for i in range(len(rows))]
        mapping = {}
        for slot_idx, row_idx in enumerate(order):
            buchstabe = buchstaben[slot_idx]
            spruch = rows[row_idx]["spruch"] or "(kein Spruch)"
            spruch_einzeiler = spruch.replace("\n", " / ")
            lines.append("**" + buchstabe + ")** " + spruch_einzeiler)
            lines.append("")
            mapping[buchstabe] = rows[row_idx]["label"]
        aufloesung.append({"seed": seed, "mapping": mapping})
        lines.append("---")
        lines.append("")

    # Aufloesung
    lines.append("## Aufloesung (Buchstabe -> Modell)")
    lines.append("")
    for eintrag in aufloesung:
        teile = [b + "=" + m for b, m in sorted(eintrag["mapping"].items())]
        lines.append("- **" + eintrag["seed"] + ":** " + " | ".join(teile))
    lines.append("")

    BLIND_CHECK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BLIND_CHECK_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\nBlind-Check gespeichert: " + str(BLIND_CHECK_PATH)
          + " (LOKAL, nicht committet)")


def main():
    print("#" * 72)
    print("# judge_models.py — Modell-Vergleich (Generator-Qualitaet)")
    print("#")
    print("# Modelle    : " + ", ".join(lbl for _, lbl in MODELS))
    print("# Seeds      : " + ", ".join(SEEDS))
    print("# fmts       : " + ", ".join(FMTS))
    print("# candidates : " + str(CANDIDATES))
    print("# min_score  : " + str(MIN_SCORE))
    print("# derbheit   : " + DERBHEIT + " | reim_strenge: " + REIM_STRENGE)
    print("# judge      : " + JUDGE_MODEL + " (KONSTANT)")
    print("# embeddings : " + str(USE_EMBEDDINGS) + " (P-Entscheid: AUS)")
    print("#" * 72)

    # API-Keys pruefen
    api_key = gen._read_api_key()
    grok_key = gen._read_grok_api_key()
    di_key = gen._read_deepinfra_api_key()
    if not api_key:
        print("ABBRUCH: Kein GLM API-Key in config.json.")
        sys.exit(1)
    if not grok_key:
        print("ABBRUCH: Kein grok_api_key in config.json.")
        sys.exit(1)
    print("API-Keys: GLM=" + ("ok" if api_key else "FEHLT")
          + " | Grok=" + ("ok" if grok_key else "FEHLT")
          + " | DeepInfra=" + ("ok" if di_key else "FEHLT"))
    print()

    # ── Hauptloop: Modell x Seed ──
    # results[model_label][seed] = result-dict
    results = {label: {} for _, label in MODELS}
    overall_t0 = time.time()

    for model_id, label in MODELS:
        print("\n\n" + "#" * 72)
        print("# MODELL: " + label + "  (id=" + model_id + ")")
        print("#" * 72)
        for i, seed in enumerate(SEEDS):
            fmt = FMTS[i]
            print("\n--- Seed " + str(i + 1) + "/" + str(len(SEEDS))
                  + ": '" + seed + "' ---")
            res = run_one(model_id, label, seed, fmt)
            results[label][seed] = res

    overall_elapsed = time.time() - overall_t0

    # ── Auswertung ──
    print("\n\n" + "=" * 72)
    print("  AUSWERTUNG")
    print("=" * 72)

    model_labels = [lbl for _, lbl in MODELS]

    # Tabelle: Seed | fmt | Judge je Modell | Delta (Challenger - Baseline)
    header = "Seed      | fmt     |"
    for lbl in model_labels:
        header += " " + lbl[:10].ljust(10) + " |"
    header += " Delta      | Spruch Challenger (gekuerzt)"
    print("\n" + header)
    print("-" * len(header))

    sum_per_model = {lbl: 0.0 for lbl in model_labels}
    n_per_model = {lbl: 0 for lbl in model_labels}
    tokens_per_model = {lbl: {"prompt": 0, "completion": 0, "gesamt": 0}
                        for lbl in model_labels}
    cost_per_model = {lbl: 0.0 for lbl in model_labels}

    for i, seed in enumerate(SEEDS):
        fmt = FMTS[i]
        row = "{:9s} | {:7s} |".format(seed, fmt)
        scores_here = []
        for lbl in model_labels:
            res = results[lbl][seed]
            js = res["judge_score"]
            if js is not None:
                sum_per_model[lbl] += js
                n_per_model[lbl] += 1
            scores_here.append(js)
            row += " " + (("{:.2f}".format(js)) if js is not None else "  -   ").ljust(10) + " |"
            # Tokens + Kosten akkumulieren
            t = res["tokens"]
            tokens_per_model[lbl]["prompt"] += t["prompt"]
            tokens_per_model[lbl]["completion"] += t["completion"]
            tokens_per_model[lbl]["gesamt"] += t["gesamt"]
            cost_per_model[lbl] += calc_cost(res["model_id"], t["prompt"], t["completion"])
        # Delta Challenger - Baseline (Modell[1] - Modell[0])
        delta_str = "  -  "
        if len(model_labels) >= 2:
            b = scores_here[0]
            c = scores_here[1]
            if b is not None and c is not None:
                delta_str = "{:+.2f}".format(c - b)
        # Spruch Challenger gekuerzt
        challenger_res = results[model_labels[-1]][seed]
        spruch_c = (challenger_res["spruch"] or "")[:50].replace("\n", " | ")
        row += " " + delta_str.ljust(10) + " | " + spruch_c
        print(row)

    # Avg je Modell
    avg_per_model = {}
    for lbl in model_labels:
        avg_per_model[lbl] = (sum_per_model[lbl] / n_per_model[lbl]
                              if n_per_model[lbl] else 0.0)

    print("\nAvg Judge je Modell:")
    for lbl in model_labels:
        print("  " + lbl.ljust(18) + ": " + "{:.2f}".format(avg_per_model[lbl])
              + "  (n=" + str(n_per_model[lbl]) + ")")
    if len(model_labels) >= 2:
        delta_avg = avg_per_model[model_labels[1]] - avg_per_model[model_labels[0]]
        print("  Delta (" + model_labels[1] + " - " + model_labels[0]
              + "): {:+.2f}".format(delta_avg))

    # Kosten je Modell
    print("\nKosten je Modell (alle 6 Seeds, gen+judge Tokens):")
    print("  (Preise/1M: " + " | ".join(
        lbl + "=$" + "{:.2f}".format(COST_PER_1M[mid]["input"]) + "/$"
        + "{:.2f}".format(COST_PER_1M[mid]["output"])
        for mid, lbl in MODELS) + " Input/Output)")
    cost_per_point = {}
    for mid, lbl in MODELS:
        t = tokens_per_model[lbl]
        print("  " + lbl.ljust(18) + ": tokens p/c/g="
              + str(t["prompt"]) + "/" + str(t["completion"]) + "/" + str(t["gesamt"])
              + " | kosten=${:.4f}".format(cost_per_model[lbl]))
        if avg_per_model[lbl] > 0:
            cost_per_point[lbl] = cost_per_model[lbl] / avg_per_model[lbl]
    if len(model_labels) >= 2 and avg_per_model.get(model_labels[0], 0) > 0:
        cost_ratio = cost_per_model[model_labels[1]] / cost_per_model[model_labels[0]] \
            if cost_per_model[model_labels[0]] > 0 else 0.0
        print("  Kosten-Verhaeltnis (" + model_labels[1] + "/" + model_labels[0]
              + "): {:.2f}x".format(cost_ratio))

    # CAVEATS
    print("\n" + "-" * 72)
    print("CAVEATS (Ergebnis = TENDENZ, kein Beweis)")
    print("-" * 72)
    print("(1) Generator zieht Klanggruppen via random.Random() OHNE Seed ->")
    print("    Modelle vergleichen NICHT identisches Reimmaterial. Delta")
    print("    enthaelt Rauschen (wie P-Lauf).")
    print("(2) grok-4.3 bewertet u.a. eigene Outputs -> moeglicher Selbstbias")
    print("    zugunsten von grok-4.3 (Challenger systematisch schlechter).")
    print("(3) Tokens inkl. konstantem Judge-Overhead (grok-4.3) in beiden")

    # Empfehlung
    print("\n" + "-" * 72)
    print("EMPFEHLUNG")
    print("-" * 72)
    if len(model_labels) >= 2:
        b_lbl = model_labels[0]
        c_lbl = model_labels[1]
        delta = avg_per_model[c_lbl] - avg_per_model[b_lbl]
        cost_b = cost_per_model[b_lbl]
        cost_c = cost_per_model[c_lbl]
        if delta > 0.25 and cost_c <= cost_b * 1.5:
            print("Challenger " + c_lbl + " ist deutlich staerker (Delta >= +0.25)")
            print("und nicht wesentlich teurer -> Wechsel erwägen.")
        elif delta > 0.25:
            print("Challenger " + c_lbl + " ist staerker (Delta {:+.2f}), aber".format(delta))
            print("deutlich teurer -> Preis-Leistung abwaegen.")
        elif abs(delta) <= 0.15:
            print("KEIN relevanter Qualitaetsunterschied (|Delta| <= 0.15).")
            print("-> " + b_lbl + " bleibt Preis-Leistungs-Optimum"
                  + (" (und ist billiger)" if cost_c >= cost_b else "."))
        else:
            print("Challenger " + c_lbl + " ist {:+.2f} vs. ".format(delta) + b_lbl + ".")
            if delta < 0:
                print("-> " + b_lbl + " bleibt die bessere Wahl (staerker + billig(er)).")
            else:
                print("Schwacher Vorteil, aber teurer -> " + b_lbl + " empfehlen.")

    print("\nGesamt-Laufzeit: " + str(round(overall_elapsed, 1)) + "s")

    # ── Blind-Check ──
    rows_per_seed = {}
    for i, seed in enumerate(SEEDS):
        rows_per_seed[seed] = []
        for _, lbl in MODELS:
            res = results[lbl][seed]
            rows_per_seed[seed].append({
                "label": lbl,
                "spruch": res["spruch"],
                "judge_score": res["judge_score"],
                "fmt": res["fmt"],
            })
    write_blind_check(rows_per_seed, model_labels)

    # ── Report speichern ──
    report = {
        "models": [{"id": mid, "label": lbl} for mid, lbl in MODELS],
        "seeds": SEEDS,
        "fmts": FMTS,
        "settings": {
            "candidates": CANDIDATES,
            "min_score": MIN_SCORE,
            "derbheit": DERBHEIT,
            "reim_strenge": REIM_STRENGE,
            "judge_model": JUDGE_MODEL,
            "use_embeddings": USE_EMBEDDINGS,
            "cost_per_1m": COST_PER_1M,
        },
        "results": {lbl: {s: results[lbl][s] for s in SEEDS}
                    for _, lbl in MODELS},
        "auswertung": {
            "avg_per_model": {lbl: round(avg_per_model[lbl], 3) for lbl in model_labels},
            "delta_avg": round(avg_per_model[model_labels[1]] - avg_per_model[model_labels[0]], 3)
                         if len(model_labels) >= 2 else None,
            "tokens_per_model": tokens_per_model,
            "cost_per_model": {lbl: round(cost_per_model[lbl], 6) for lbl in model_labels},
            "gesamt_laufzeit_s": round(overall_elapsed, 1),
        },
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("\nReport gespeichert: " + str(REPORT_PATH))


if __name__ == "__main__":
    main()
