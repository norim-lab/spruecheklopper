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
      zufaellig durchmischt als A) ... / B) ... / C) ... / D) ..., Aufloesung
      am Ende. (LOKAL — wird NICHT committet, menschliche Ground-Truth.)

Voraussetzungen:
    - config.json mit grok_api_key + deepinfra_api_key
    - generate_spruch_best + session_stats/session_reset aus spruch_app.generator

CAVEATS (Ergebnis ist TENDENZ, nicht Beweis):
    (1) Der Generator zieht Klanggruppen via random.Random() OHNE Seed ->
        die Modelle vergleichen NICHT identisches Reimmaterial. Das Delta
        enthaelt also Klang-Rauschen (wie beim P-Lauf).
    (2) grok-4.3 bewertet u.a. eigene Outputs -> moeglicher Selbstbias
        zugunsten von grok-4.3. Der Blind-Check (output/blind_check.md)
        ohne Modell-Label ist der menschliche Tiebreaker.
    (3) session_stats() erfasst Generator- UND Judge-Tokens zusammen. Da der
        Judge (grok-4.3) konstant ist, ist sein Token-Overhead in allen
        Modellen ~gleich — das Delta der Totaltokens entspricht dem
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
# VORARBEIT-Ergebnis (live geprueft 2026-06-16, alle 200 OK via Dispatcher-Fix):
#   grok-4.3                     -> 200 OK (xAI)              -- Baseline/Kontrolle
#   anthropic/claude-opus-4-8    -> 200 OK (DeepInfra)        -- AUSGEKLAMMERT (Budget-Sprenger)
#   google/gemini-3.1-pro        -> 200 OK (DeepInfra)        -- Challenger
#   deepseek-ai/DeepSeek-V4-Pro  -> 200 OK (DeepInfra)        -- Challenger
#   gpt-5.4                      -> n/a (kein OpenAI-Key in config.json)
# HINWEIS: Claude-Opus-4.8 ($15/$75 pro 1M) wurde nach zwei Budget-Sprengungen
# (402 Payment Required mitten im Lauf) entfernt. Lässt sich bei Bedarf separat
# nachholen, sobald das DeepInfra-Guthaben großzuegig dimensioniert ist.
MODELS = [
    ("grok-4.3",                    "grok-4.3"),        # Baseline (= Judge)
    ("google/gemini-3.1-pro",       "Gemini-3.1-Pro"),  # Challenger
    ("deepseek-ai/DeepSeek-V4-Pro", "DeepSeek-V4-Pro"), # Challenger
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
# Quelle: Hersteller-/DeepInfra-Public-Pricing Stand 2026-06.
# Grok per Spec-Vorgabe; die uebrigen via DeepInfra-Listing.
COST_PER_1M = {
    "grok-4.3":                    {"input": 1.25, "output": 2.50},
    "anthropic/claude-opus-4-8":   {"input": 15.0, "output": 75.0},
    "google/gemini-3.1-pro":       {"input": 2.00, "output": 12.0},
    "deepseek-ai/DeepSeek-V4-Pro": {"input": 2.00, "output": 8.00},
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


def _auswertung(results, model_labels_done, overall_elapsed, partial=False):
    """Berechnet Auswertung fuer bereits fertige Modelle.

    model_labels_done: Liste der Labels, die mindestens einen Seed haben.
    partial: True = nur Teilmenge fertig (fuer inkrementellen Report).
    Gibt Dict mit allen Auswertungswerten zurueck.
    """
    baseline_lbl = model_labels_done[0] if model_labels_done else None

    sum_per_model = {lbl: 0.0 for lbl in model_labels_done}
    n_per_model = {lbl: 0 for lbl in model_labels_done}
    tokens_per_model = {lbl: {"prompt": 0, "completion": 0, "gesamt": 0}
                        for lbl in model_labels_done}
    cost_per_model = {lbl: 0.0 for lbl in model_labels_done}

    for seed in SEEDS:
        for lbl in model_labels_done:
            if seed not in results[lbl]:
                continue
            res = results[lbl][seed]
            js = res["judge_score"]
            if js is not None:
                sum_per_model[lbl] += js
                n_per_model[lbl] += 1
            t = res["tokens"]
            tokens_per_model[lbl]["prompt"] += t["prompt"]
            tokens_per_model[lbl]["completion"] += t["completion"]
            tokens_per_model[lbl]["gesamt"] += t["gesamt"]
            cost_per_model[lbl] += calc_cost(res["model_id"], t["prompt"], t["completion"])

    avg_per_model = {lbl: (sum_per_model[lbl] / n_per_model[lbl]
                           if n_per_model[lbl] else 0.0)
                     for lbl in model_labels_done}
    cost_per_point = {lbl: (cost_per_model[lbl] / avg_per_model[lbl]
                            if avg_per_model.get(lbl, 0) > 0 else 0.0)
                      for lbl in model_labels_done}

    ranking = sorted(model_labels_done, key=lambda l: avg_per_model[l], reverse=True)

    delta_vs_baseline = {}
    if baseline_lbl:
        delta_vs_baseline = {lbl: round(avg_per_model[lbl] - avg_per_model[baseline_lbl], 3)
                             for lbl in model_labels_done if lbl != baseline_lbl}

    return {
        "avg_per_model": {lbl: round(avg_per_model[lbl], 3) for lbl in model_labels_done},
        "delta_vs_grok": delta_vs_baseline,
        "ranking": ranking,
        "tokens_per_model": tokens_per_model,
        "cost_per_model": {lbl: round(cost_per_model[lbl], 6) for lbl in model_labels_done},
        "cost_per_point": {lbl: round(cost_per_point[lbl], 6) for lbl in model_labels_done},
        "gesamt_laufzeit_s": round(overall_elapsed, 1),
        "partial": partial,
        "fertige_modelle": len(model_labels_done),
        "gesamt_modelle": len(MODELS),
    }


def _save_report(results, model_labels_done, overall_elapsed, partial=False):
    """Schreibt Report-JSON (inkrementell — nach jedem Modell aufrufbar).

    Schreibt nur Ergebnisse fuer fertige Modelle. Bei Abbruch mitten in einem
    Modell enthaelt der Report das Modell zwar nicht, aber alle vorherigen.
    Das ist der Ausfallschutz gegenueber dem alten "nur-am-Ende"-Verhalten.
    """
    ausw = _auswertung(results, model_labels_done, overall_elapsed, partial=partial)
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
        "results": {lbl: {s: results[lbl][s] for s in SEEDS if s in results.get(lbl, {})}
                    for lbl in model_labels_done},
        "auswertung": ausw,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = REPORT_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    tmp.replace(REPORT_PATH)  # atomar: kein halbes File bei Abbruch waehrend Write


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

    for model_idx, (model_id, label) in enumerate(MODELS):
        print("\n\n" + "#" * 72)
        print("# MODELL " + str(model_idx + 1) + "/" + str(len(MODELS))
              + ": " + label + "  (id=" + model_id + ")")
        print("#" * 72)
        for i, seed in enumerate(SEEDS):
            fmt = FMTS[i]
            print("\n--- Seed " + str(i + 1) + "/" + str(len(SEEDS))
                  + ": '" + seed + "' ---")
            res = run_one(model_id, label, seed, fmt)
            results[label][seed] = res

            # INKREMENTELLER SPEICHER: nach JEDEM Seed Report aktualisieren.
            # So ueberlebt ein Abbruch (Crash, Ctrl-C, API-Ausfall, 402) alle
            # bisherigen Seeds incl. TEILmodelle (z.B. Gemini mit 3/6 Seeds).
            # done_labels = alle Modelle mit >=1 fertigem Seed (Teilmodelle OK).
            done_labels = [lbl for _, lbl in MODELS
                           if len(results.get(lbl, {})) >= 1]
            elapsed_now = time.time() - overall_t0
            total_seeds_done = sum(len(results.get(lbl, {}))
                                   for _, lbl in MODELS)
            total_seeds_all = len(MODELS) * len(SEEDS)
            is_final = (total_seeds_done == total_seeds_all)
            _save_report(results, done_labels, elapsed_now,
                         partial=(not is_final))
            if not is_final:
                print("[inkrementell] gespeichert: "
                      + str(total_seeds_done) + "/" + str(total_seeds_all)
                      + " Seeds (" + str(len(done_labels)) + "/"
                      + str(len(MODELS)) + " Modelle angefangen) -> "
                      + str(REPORT_PATH))

    overall_elapsed = time.time() - overall_t0

    # ── Auswertung ──
    print("\n\n" + "=" * 72)
    print("  AUSWERTUNG")
    print("=" * 72)

    model_labels = [lbl for _, lbl in MODELS]
    ausw = _auswertung(results, model_labels, overall_elapsed, partial=False)
    avg_per_model = {lbl: ausw["avg_per_model"][lbl] for lbl in model_labels}
    tokens_per_model = ausw["tokens_per_model"]
    cost_per_model = ausw["cost_per_model"]
    cost_per_point = ausw["cost_per_point"]
    ranking = ausw["ranking"]
    baseline_lbl = model_labels[0]

    # Tabelle: Seed | fmt | Judge je Modell
    header = "Seed      | fmt     |"
    for lbl in model_labels:
        header += " " + lbl[:14].ljust(14) + " |"
    print("\n" + header)
    print("-" * len(header))
    for i, seed in enumerate(SEEDS):
        fmt = FMTS[i]
        row = "{:9s} | {:7s} |".format(seed, fmt)
        for lbl in model_labels:
            res = results[lbl][seed]
            js = res["judge_score"]
            row += " " + (("{:.2f}".format(js)) if js is not None else "  -   ").ljust(14) + " |"
        print(row)

    # Ranking (sortiert nach avg_score absteigend)
    print("\n" + "-" * 72)
    print("RANKING (nach Avg-Judge-Score, grok-4.3 = Baseline)")
    print("-" * 72)
    print("  Rang | Modell              | Avg  | Delta vs grok | Tokens(ges) | Kosten    | $/Punkt")
    for rang, lbl in enumerate(ranking, 1):
        avg = avg_per_model[lbl]
        delta = avg - avg_per_model[baseline_lbl]
        tg = tokens_per_model[lbl]["gesamt"]
        cost = cost_per_model[lbl]
        cpp = cost_per_point[lbl]
        marker = " <- Baseline" if lbl == baseline_lbl else ""
        print("  {:>3d}  | {:18s} | {:.2f} | {:+.2f}          | {:>10d} | ${:>7.4f} | ${:.4f}{}".format(
            rang, lbl, avg, delta, tg, cost, cpp, marker))

    # Preis-Tabelle
    print("\nPreise/1M Tokens (Input/Output):")
    for mid, lbl in MODELS:
        p = COST_PER_1M.get(mid, {"input": 0, "output": 0})
        print("  " + lbl.ljust(18) + ": $" + "{:.2f}".format(p["input"])
              + " / $" + "{:.2f}".format(p["output"]))

    # CAVEATS
    print("\n" + "-" * 72)
    print("CAVEATS (Ergebnis = TENDENZ, kein Beweis)")
    print("-" * 72)
    print("(1) Generator zieht Klanggruppen via random.Random() OHNE Seed ->")
    print("    Modelle vergleichen NICHT identisches Reimmaterial. Delta")
    print("    enthaelt Rauschen (wie P-Lauf).")
    print("(2) grok-4.3 bewertet u.a. eigene Outputs -> moeglicher Selbstbias")
    print("    zugunsten von grok-4.3 (Challenger systematisch schlechter).")
    print("    Blind-Check (output/blind_check.md) ist der menschliche Tiebreaker.")

    # Empfehlung
    print("\n" + "-" * 72)
    print("EMPFEHLUNG")
    print("-" * 72)
    best_lbl = ranking[0]
    best_delta = avg_per_model[best_lbl] - avg_per_model[baseline_lbl]
    if best_lbl == baseline_lbl:
        print("Kein Challenger schlaegt grok-4.3 (" + baseline_lbl + " bleibt Platz 1).")
        print("-> grok-4.3 ist das Preis-Leistungs-Optimum.")
    elif best_delta > 0.25:
        print("Staerkster Challenger: " + best_lbl + " (Delta {:+.2f} vs grok-4.3).".format(best_delta))
        cpp_best = cost_per_point[best_lbl]
        cpp_grok = cost_per_point[baseline_lbl]
        if cpp_best <= cpp_grok:
            print("Zudem guenstiger pro Punkt -> Wechsel empfohlen.")
        else:
            print("Aber " + "{:.1f}x".format(cpp_best / cpp_grok if cpp_grok > 0 else 0)
                  + " teurer pro Punkt -> Preis-Leistung abwaegen.")
    else:
        print("Challenger-Vorsprung marginal (|Delta| <= 0.25).")
        print("-> grok-4.3 bleibt Preis-Leistungs-Optimum (Billigster + vertraut).")

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

    # ── Finaler Report (inkrementeller Write bereits nach jedem Modell,
    # hier nochmals final, partial=False) ──
    _save_report(results, model_labels, overall_elapsed, partial=False)
    print("\nReport gespeichert: " + str(REPORT_PATH))


if __name__ == "__main__":
    main()
