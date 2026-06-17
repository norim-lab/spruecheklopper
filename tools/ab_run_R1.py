"""tools/ab_run_R1.py -- R.1: 3-armiger A/B-Lauf (Pointe-zuerst-Kern).

ZIEL (R.1): Pruefen, ob der neue Pointe-zuerst-Kern den menschlichen Blind-Check
ueber den 1,47-Boden hebt. Drei Arme unter IDENTISCHEN Startbedingungen, nur
der System-Prompt unterscheidet sie:

    A = Kontrolle : bestehender SYSTEM_PROMPT        (reim-zuerst), UNVERAENDERT
    B = SYSTEM_PROMPT_V2                              (Pointe-zuerst, neutral)
    C = SYSTEM_PROMPT_V3                              (Pointe-zuerst, gewichtet)

VORARBEIT (Pflicht, in diesem Header festgehalten):
    (1) Der aktuelle SYSTEM_PROMPT liegt in spruch_app/generator.py (1625-1861).
        generate_spruch() baut daraus system_prompt_full =
        SYSTEM_PROMPT + _build_derbheit_block(derbheit) + dyn_examples
        (Zeile 2536).  generate_spruch_best() ruft generate_spruch() 8x mit
        demselben rnd -- deshalb bekommt jeder der 8 Kandidaten NEUE
        Klanggruppen aus _pick_seed_v2(rnd,...).  Der Sieger = hoechster
        Judge-Score der 8.
    (2) Q.2 nutzt RUN_SEED=42 und rnd=random.Random(f"{RUN_SEED}:{seed}") ->
        deterministische Klanggruppen/Wort-Sampling. Dieselbe Mechanik hier.
        Zusaetzlich: variance_state.json wird VOR JEDEM (seed, arm)-Call
        resettet (Q.2-Praxis), und _update_variance wird fuer den Lauf
        no-op gepatched, damit Arm B/C denselben penalized_klang-Satz
        sehen wie Arm A.

KEIN APP-DEFAULT-CHANGE: generator.SYSTEM_PROMPT (Modul-Global) wird pro
Arm WIEDERHERSTELLBAR ueberschrieben.  Der App-Default bleibt unangetastet.

OUTPUT:
    output/ab_R1_raw.json     - alle 18 Sieger + Klanggruppen-Log + Verifikation
    output/blind_check_R1.md  - pro Seed 3 Sieger anonym, randomisiert via
                                random.Random("42:blind"), Aufloesung unten,
                                Bewertungszeilen Reim/Witz/Bild/Ton je 1-5.
"""

import json
import os
import random
import sys
import time
import traceback
from pathlib import Path

# Pfad-Setup: Projekt-Root in sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spruch_app import generator as gen


# ── Fixe Konfiguration ──────────────────────────────────────────────────────

RUN_SEED       = 42
N_SEEDS        = 6
CANDIDATES     = 8
MIN_SCORE      = 4
MODEL          = "grok-4.3"
JUDGE_MODEL    = "grok-4.3"
DERBHEIT       = "derb"
REIM_STRENGE   = "DB-streng"
FMT_REQUEST    = "AA-2"   # 2-Zeiler Paarreim, wie V2/V3 fordern
MODE           = "short"
USE_EMBEDDINGS = False

OUTPUT_DIR     = ROOT / "output"
RAW_PATH       = OUTPUT_DIR / "ab_R1_raw.json"
BLIND_PATH     = OUTPUT_DIR / "blind_check_R1.md"
VARIANCE_PATH  = OUTPUT_DIR / "variance_state.json"
SEEDS_JSON     = OUTPUT_DIR / "seed_woerter_v22.json"


# ── Q.2-Determinismus-Fix: _update_variance zu No-Op patchen ────────────────
# Grund: waehrend generate_spruch_best wuerde _update_variance die akzeptierten
# Klanggruppen in variance_state.json schreiben -> Arm B/C saehen anderen
# penalized_klang als Arm A. No-Op fuer den Messlauf + Reset vor jedem Call.

def _noop_update_variance(*args, **kwargs):
    return None


gen._update_variance = _noop_update_variance


def reset_variance_state():
    """Setzt variance_state.json auf leeren State (Defense-in-Depth)."""
    leer = {"last_10_personas": [], "last_10_settings": [],
            "last_20_klang_gruppen": [], "last_20_reimwoerter": []}
    try:
        VARIANCE_PATH.write_text(
            json.dumps(leer, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print("  [warn] variance reset failed: " + str(e))


# ── Klanggruppen-Recorder: hook _pick_seed_v2 ──────────────────────────────
# Beweisst: welche Klanggruppen wurden pro (seed, arm) WIRKLICH angeboten?
# Die LLM-zurueckgegebenen Labels sind nicht vertrauenswuerdig (Q.2-Lesson).
# Wir loggen den Output des originaeren _pick_seed_v2.

_KLANG_LOG = []


def _install_klang_recorder():
    orig = gen._pick_seed_v2

    def wrapper(rnd, fmt="AABB-4", thema=None, use_embeddings=gen.USE_EMBEDDINGS_DEFAULT):
        gruppen = orig(rnd, fmt=fmt, thema=thema, use_embeddings=use_embeddings)
        try:
            labels = [g["klang"] for g in gruppen] if gruppen else []
            woerter = [g.get("woerter", []) for g in gruppen] if gruppen else []
        except Exception:
            labels, woerter = [], []
        _KLANG_LOG.append({"fmt": fmt, "labels": labels, "woerter": woerter})
        return gruppen

    gen._pick_seed_v2 = wrapper


# ── 6 Seeds deterministisch ziehen ──────────────────────────────────────────

def draw_seeds():
    data = json.loads(SEEDS_JSON.read_text(encoding="utf-8"))
    pool = data["seeds"]
    r = random.Random("{}:R1-seeds".format(RUN_SEED))
    return r.sample(pool, N_SEEDS)


# ── Inkrementelles Speichern (Crash-Safe via tmp + replace) ─────────────────

def save_raw(raw):
    tmp = RAW_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(RAW_PATH)


# ── Arme ────────────────────────────────────────────────────────────────────

ARMS = [
    ("A", "Kontrolle (SYSTEM_PROMPT, reim-zuerst)",   gen.SYSTEM_PROMPT),
    ("B", "Pointe-zuerst neutral (SYSTEM_PROMPT_V2)", gen.SYSTEM_PROMPT_V2),
    ("C", "Pointe-zuerst gewichtet (SYSTEM_PROMPT_V3)", gen.SYSTEM_PROMPT_V3),
]

# Defense: Assert dass V2/V3 existieren (sonst Refresh der generator.py noetig)
for _aid, _name, _p in ARMS:
    assert _p, "System-Prompt fuer Arm {} fehlt!".format(_aid)
assert gen.SYSTEM_PROMPT_V2 != gen.SYSTEM_PROMPT, "V2 == V1 (Default)"
assert gen.SYSTEM_PROMPT_V3 != gen.SYSTEM_PROMPT, "V3 == V1 (Default)"
assert gen.SYSTEM_PROMPT_V2 != gen.SYSTEM_PROMPT_V3, "V2 == V3"


# ── Hauptlauf ───────────────────────────────────────────────────────────────

def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    seeds = draw_seeds()
    print("=" * 72)
    print("R.1 A/B-Lauf | RUN_SEED={} | {} Seeds aus v22 (gezogen via Random('{}:R1-seeds'))".format(
        RUN_SEED, N_SEEDS, RUN_SEED))
    for i, s in enumerate(seeds, 1):
        print("  {}. {}".format(i, s))
    print("Arme:")
    for aid, name, _ in ARMS:
        print("  {} = {}".format(aid, name))
    print("=" * 72)

    raw = {
        "meta": {
            "auftrag": "R.1",
            "ziel": "Prueft, ob der Pointe-zuerst-Kern den menschlichen "
                    "Blind-Check ueber den 1,47-Boden hebt.",
            "run_seed": RUN_SEED,
            "n_seeds": N_SEEDS,
            "candidates": CANDIDATES,
            "min_score": MIN_SCORE,
            "model": MODEL,
            "judge_model": JUDGE_MODEL,
            "derbheit": DERBHEIT,
            "reim_strenge": REIM_STRENGE,
            "fmt_request": FMT_REQUEST,
            "mode": MODE,
            "use_embeddings": USE_EMBEDDINGS,
            "arms": {aid: name for aid, name, _ in ARMS},
        },
        "seeds": seeds,
        "results": {},           # results[seed][arm_id] = {...}
        "klanggruppen_log": {},  # klanggruppen_log[seed][arm_id] = [[labels], ...]
    }
    save_raw(raw)

    _install_klang_recorder()
    _orig_default = gen.SYSTEM_PROMPT  # App-Default, am Ende wiederherstellen

    t_start = time.time()
    n_done = 0

    for seed in seeds:
        raw["results"][seed] = {}
        raw["klanggruppen_log"][seed] = {}

        for arm_id, arm_name, arm_prompt in ARMS:
            n_done += 1
            print("\n[{:02d}/18] Seed={!r} Arm={} ({})".format(
                n_done, seed, arm_id, arm_name))

            global _KLANG_LOG
            _KLANG_LOG.clear()

            # ── Pro-Arm-Setup ──
            gen.SYSTEM_PROMPT = arm_prompt     # Monkey-Patch (pro Arm)
            reset_variance_state()
            gen._GEN_STATUS["cancel"] = False
            try:
                gen._clear_grok_usage_log()
            except Exception:
                pass

            rnd = random.Random("{}:{}".format(RUN_SEED, seed))
            t0 = time.time()
            try:
                best = gen.generate_spruch_best(
                    mode=MODE,
                    candidates=CANDIDATES,
                    min_score=MIN_SCORE,
                    model=MODEL,
                    judge_model=JUDGE_MODEL,
                    thema=seed,
                    derbheit=DERBHEIT,
                    reim_strenge=REIM_STRENGE,
                    fmt_request=FMT_REQUEST,
                    use_embeddings=USE_EMBEDDINGS,
                    rnd=rnd,
                    no_fallback=True,
                )
            except Exception as e:
                traceback.print_exc()
                best = {"ok": False,
                        "error": "Exception: {}".format(e)[:200],
                        "spruch": "", "reimwoerter": [], "klang_gruppen": []}
            dt = time.time() - t0

            judge_score = best.get("judge_score", best.get("self_score", 0))
            try:
                judge_score = float(judge_score)
            except (TypeError, ValueError):
                judge_score = 0.0

            entry = {
                "arm": arm_id,
                "arm_name": arm_name,
                "seed": seed,
                "ok": bool(best.get("ok", False)),
                "spruch": best.get("spruch", ""),
                "reimwoerter": best.get("reimwoerter", []),
                "klang_gruppen": best.get("klang_gruppen", []),
                "letztes_wort": best.get("letztes_wort", ""),
                "judge_score": judge_score,
                "self_score": best.get("self_score", best.get("score", 0)),
                "judge_begruendung": best.get("judge_begruendung", ""),
                "model": best.get("model", MODEL),
                "error": best.get("error", ""),
                "dauer_s": round(dt, 1),
                "kosten_usd": round(float(best.get("kosten_usd", 0.0) or 0.0), 6),
                # WIRKLICH angebotene Klanggruppen (Hook):
                "klanggruppen_calls": [list(x["labels"]) for x in _KLANG_LOG],
                "reimwoerter_calls": [list(x["woerter"]) for x in _KLANG_LOG],
            }
            raw["results"][seed][arm_id] = entry
            raw["klanggruppen_log"][seed][arm_id] = entry["klanggruppen_calls"]
            save_raw(raw)

            print("   ok={} | judge_score={} | dauer={:.1f}s | kosten=${:.4f}".format(
                entry["ok"], judge_score, dt, entry["kosten_usd"]))
            print("   Spruch: {}".format(entry["spruch"][:140]))
            print("   reimwoerter: {}".format(entry["reimwoerter"]))
            print("   angebotene Klanggruppen (8x): {}".format(
                entry["klanggruppen_calls"]))

    # App-Default wiederherstellen (KEINE dauerhafte Aenderung)
    gen.SYSTEM_PROMPT = _orig_default

    total_dt = time.time() - t_start
    print("\n" + "=" * 72)
    print("Lauf beendet in {:.1f}s ({:.1f} min)".format(total_dt, total_dt / 60))
    print("=" * 72)

    # ── Verifikation 1: Klanggruppen pro Seed ueber alle 3 Arme identisch ──
    print("\nVERIFIKATION: angebotene Klanggruppen pro Seed ueber alle 3 Arme")
    print("-" * 72)
    verification = {}
    for seed in seeds:
        per_arm = raw["klanggruppen_log"][seed]
        ref_a = per_arm.get("A", [])
        ref_b = per_arm.get("B", [])
        ref_c = per_arm.get("C", [])
        ab_match = ref_a == ref_b
        ac_match = ref_a == ref_c
        all_match = ab_match and ac_match
        verification[seed] = {
            "identical_across_arms": all_match,
            "A_eq_B": ab_match,
            "A_eq_C": ac_match,
            "groups_offered_arm_A": ref_a,
        }
        print("Seed={!r}: identical_across_arms={}  (A==B: {}, A==C: {})".format(
            seed, all_match, ab_match, ac_match))
        if all_match:
            print("   -> 8 Klanggruppen-Calls pro Arm IDENTISCH (Delta = Prompt-Effekt)")
            for i, labels in enumerate(ref_a, 1):
                print("      Call {}: {}".format(i, labels))
        else:
            print("   !! WARNUNG: Arme sehen unterschiedliche Klanggruppen!")
            print("      A: {}".format(ref_a))
            print("      B: {}".format(ref_b))
            print("      C: {}".format(ref_c))

    raw["verification"] = verification
    save_raw(raw)

    # ── Verifikation 2: ok-Quote (0 Reim-Rejects bei den Siegern) ───────────
    n_ok = sum(1 for seed in seeds for aid, _, _ in ARMS
               if raw["results"][seed][aid]["ok"])
    n_total = len(seeds) * len(ARMS)
    print("\nVERIFIKATION: Sieger ok=True (validate_spruch-gruen)")
    print("-" * 72)
    print("{}/{} Sieger ok=True".format(n_ok, n_total))
    if n_ok < n_total:
        for seed in seeds:
            for aid, _, _ in ARMS:
                e = raw["results"][seed][aid]
                if not e["ok"]:
                    print("  FAIL: seed={!r} arm={} error={}".format(
                        seed, aid, e.get("error", "")))

    # ── Konsolen-Summary: grok-Judge-Schnitt je Arm ─────────────────────────
    print("\n" + "=" * 72)
    print("JUDGE-SCHNITT JE ARM ({} als Judge)".format(JUDGE_MODEL))
    print("=" * 72)
    arm_scores = {aid: [] for aid, _, _ in ARMS}
    for seed in seeds:
        for aid, _, _ in ARMS:
            sc = raw["results"][seed][aid].get("judge_score")
            if isinstance(sc, (int, float)) and sc > 0:
                arm_scores[aid].append(float(sc))
    for aid, name, _ in ARMS:
        scs = arm_scores[aid]
        avg = sum(scs) / len(scs) if scs else 0.0
        print("  Arm {} ({}): avg={:.2f} (n={}/{})".format(
            aid, name, avg, len(scs), len(seeds)))
        print("     single scores: {}".format(scs))
    baseline = (sum(arm_scores["A"]) / len(arm_scores["A"])
                if arm_scores["A"] else 0.0)
    for aid in ("B", "C"):
        scs = arm_scores[aid]
        avg = sum(scs) / len(scs) if scs else 0.0
        delta = avg - baseline
        print("  Delta Arm {} vs A: {:+.2f}".format(aid, delta))

    # ── blind_check_R1.md schreiben ─────────────────────────────────────────
    write_blind(raw, seeds)
    print("\nGeschrieben: {}".format(RAW_PATH))
    print("Geschrieben: {}".format(BLIND_PATH))


def write_blind(raw, seeds):
    """output/blind_check_R1.md: pro Seed 3 Sieger anonym + randomisiert."""
    blind_rnd = random.Random("{}:blind".format(RUN_SEED))
    lines = []
    lines.append("# R.1 Blind-Check - Pointe-zuerst-Kern")
    lines.append("")
    lines.append("{} Seeds x 3 Arme = 18 Sprueche. Pro Seed neu randomisiert "
                 "(Seed={}). KEIN Arm-Name im anonymen Teil.".format(
                     len(seeds), RUN_SEED))
    lines.append("")
    lines.append("Bewerte jeden Spruch auf 4 Achsen (1-5): "
                 "Reim / Witz / Bild / Ton.")
    lines.append("")
    lines.append("---")
    lines.append("")
    aufloesung = []
    for seed in seeds:
        lines.append("## Seed: {}".format(seed))
        lines.append("")
        drei = [(aid, raw["results"][seed][aid]) for aid, _, _ in ARMS]
        blind_rnd.shuffle(drei)
        for i, (arm_id, entry) in enumerate(drei):
            label = chr(65 + i)  # A, B, C
            spruch = entry["spruch"] or "(kein Output)"
            lines.append("**{})**".format(label))
            lines.append("")
            for zeile in spruch.splitlines():
                lines.append("> " + zeile)
            lines.append("")
            lines.append("- Reim: __ / Witz: __ / Bild: __ / Ton: __")
            lines.append("")
            aufloesung.append("{}  -  {} = Arm {} ({})".format(
                seed, label, arm_id, raw["meta"]["arms"][arm_id]))
        lines.append("---")
        lines.append("")
    lines.append("## Aufloesung (erst NACH Bewertung lesen!)")
    lines.append("")
    for zeile in aufloesung:
        lines.append("- " + zeile)
    lines.append("")
    BLIND_PATH.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
