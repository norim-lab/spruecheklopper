"""tools/ab_run_R1b.py -- R.1b: 3-armiger A/B-Lauf (Charge-2-Stil, Zweizeiler vs. Vierzeiler).

ZIEL (R.1b): Pruefen, ob der Charge-2-Stil (V4: Ueberraschung statt Schock,
haeufige Woerter, optional Vierzeiler) den menschlichen Blind-Check ueber den
1,47-Boden hebt, den R.1 (Pointe-zuerst-V2/V3) NICHT gehoben hat. Hauptursache
in R.1: 15/18 Sprueche kippten in monotone Sex-/Penetrations-Pointen.

R.1b dreht VIER Hebel (gegenueber R.1):
    1. NUR HAEUFIGE Woerter       -> MAX_HAEUFIGKEIT=5 (statt 18) fuer B/C
    2. Charge-2-Stil              -> SYSTEM_PROMPT_V4_ZWEI / V4_VIER + FEWSHOT_V4
                                     (Ueberraschung > Schock; KEINE Sex-Beispiele)
    3. VIERZEILER-Arm             -> Arm C: 4 Zeilen, AABB
    4. SEX-CAP (Harness)          -> Blockliste sexueller Schlusswoerter; unter
                                     8 Kandidaten den besten nicht-sexuellen waehlen

DREI ARME (nur System-Prompt + MAX_HAEUFIGKEIT + fmt unterscheiden sich):
    A = Kontrolle : SYSTEM_PROMPT (Default, reim-zuerst), MAX_HAEUFIGKEIT=18, AA-2
    B = V4_ZWEI    : SYSTEM_PROMPT_V4_ZWEI + FEWSHOT_V4, MAX_HAEUFIGKEIT=5, AA-2
    C = V4_VIER    : SYSTEM_PROMPT_V4_VIER + FEWSHOT_V4, MAX_HAEUFIGKEIT=5, AABB-4

VORARBEIT (Pflicht, hier festgehalten — nachgelesen in spruch_app/generator.py):
    (1) SYSTEM_PROMPT liegt in generator.py (Default). generate_spruch() baut
        system_prompt_full = SYSTEM_PROMPT + _build_derbheit_block(derbheit)
        + dyn_examples (Zeile ~2536). Pro Arm via Monkey-Patch
        gen.SYSTEM_PROMPT = arm_prompt ueberschreibbar (bewaehrt in R.1).
    (2) Reim-Primitive fuer EIN Paar:
            _check_reimpaar(w1, w2, ipa_map, zeile_label, gruppe_partner=None,
                            reim_strenge="DB-streng")  (generator.py:2350)
        3 Stufen: DB-Partnerliste -> IPA -> _reim_endung-Heuristik.
        validate_spruch nutzt sie in der Paarreim-Branche (Zeile 2458-2470) fuer
        (Z1,Z2) mit gA und (Z3,Z4) mit gB. Genau diese Primitive wendet
        validate_vierzeiler() (lokale Funktion, s.u.) auf (Z1,Z2) UND (Z3,Z4) an.
    (3) MAX_HAEUFIGKEIT = 18 (generator.py:76/98). Filter in
        _pick_seed_v2_aus_kuratisiert (Zeile 1515-1518):
            haeuf = r.get("haeufigkeit", None)
            if haeuf is not None and haeuf > MAX_HAEUFIGKEIT: continue
        _pick_seed_v2 (Zeile 1401) ist das Gateway. Da der Filter live
        MAX_HAEUFIGKEIT liest, greift pro-Arm-Setzung via gen.MAX_HAEUFIGKEIT.

KEIN APP-DEFAULT-CHANGE: generator.SYSTEM_PROMPT + generator.MAX_HAEUFIGKEIT
werden pro Arm WIEDERHERSTELLBAR ueberschrieben. validate_spruch wird NICHT
geaendert (Vierzeiler-Pruefung ist lokale Harness-Funktion).

OUTPUT:
    output/ab_R1b_raw.json    - alle Kandidaten je (Arm,Seed) + Sieger +
                                Klanggruppen-Log + SEX-CAP-Statistik +
                                Vierzeiler-Validierung + Verifikation
    output/blind_check_R1b.md - pro Seed 3 Sieger anonym, randomisiert via
                                random.Random("42:blind"), Aufloesung unten,
                                Bewertungszeilen Reim/Witz/Bild/Ton je 1-5.
"""

import json
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

RUN_SEED     = 42
N_SEEDS      = 6
CANDIDATES   = 8
MIN_SCORE    = 4
MODEL        = "grok-4.3"
JUDGE_MODEL  = "grok-4.3"
DERBHEIT     = "derb"
REIM_STRENGE = "DB-streng"

OUTPUT_DIR     = ROOT / "output"
RAW_PATH       = OUTPUT_DIR / "ab_R1b_raw.json"
BLIND_PATH     = OUTPUT_DIR / "blind_check_R1b.md"
VARIANCE_PATH  = OUTPUT_DIR / "variance_state.json"
SEEDS_JSON     = OUTPUT_DIR / "seed_woerter_v22.json"

# ── SEX-CAP: Blockliste sexueller / Penetrations-Schlusswoerter ─────────────
# Spec: "loch, schoss, saft, schaft, gemaecht, stiel, gelenk, ... erweiterbar".
# Unter 8 Kandidaten den mit hoechstem judge_score waehlen, dessen Schlusswort
# NICHT hier drin steht. Nur falls ALLE geblockt -> hoechsten ueberhaupt, und
# sex_capped=True im raw-JSON markieren.
SEX_BLOCKLIST = {
    # Spec-Vorgabe
    "loch", "schoss", "saft", "schaft", "gemaecht", "stiel", "gelenk",
    # Erweiterungen (klare sexuelle / penetrative Doppeldeutigkeit als Schlusswort)
    "spalt", "zapfen", "rute", "kolben", "pfahl", "euter", "moese",
    "pimmel", "schwanz", "hodensack", "rosenknospe",
}


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
# Beweist: welche Klanggruppen wurden pro (seed, arm) WIRKLICH angeboten?
# (Die LLM-zurueckgegebenen Labels sind nicht vertrauenswuerdig — Q.2-Lesson.)

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


# ── Judge-Pool-Recorder: hook _judge_sprueche ──────────────────────────────
# SEX-CAP braucht ALLE 8 Kandidaten mit judge_scores (nicht nur den Sieger,
# den generate_spruch_best intern waehlt). Wir fangen den pool + scores ab,
# ohne generate_spruch_best oder _judge_sprueche zu aendern.

_JUDGE_POOL_LOG = []


def _install_judge_recorder():
    orig = gen._judge_sprueche

    def wrapper(kandidaten, model=gen.JUDGE_MODEL, derbheit="derb", conv_id=None):
        urteil = orig(kandidaten, model=model, derbheit=derbheit, conv_id=conv_id)
        scores = urteil.get("scores", []) if urteil else []
        pool_copy = []
        for i, r in enumerate(kandidaten):
            pool_copy.append({
                "spruch": r.get("spruch", ""),
                "letztes_wort": r.get("letztes_wort", ""),
                "reimwoerter": r.get("reimwoerter", []),
                "klang_gruppen": r.get("klang_gruppen", []),
                "self_score": r.get("self_score", r.get("score", 0)),
                "judge_score": (float(scores[i])
                                if scores and i < len(scores)
                                else 0.0),
                "ok": r.get("ok", False),
                "last_resort": r.get("last_resort", False),
            })
        _JUDGE_POOL_LOG.append({
            "pool": pool_copy,
            "best_index": urteil.get("best_index") if urteil else 0,
            "begruendung": urteil.get("begruendung", "") if urteil else "",
        })
        return urteil

    gen._judge_sprueche = wrapper


# ── Schlusswort-Extraktion + SEX-CAP ───────────────────────────────────────

def _extract_schlusswort(spruch):
    """ALLERLETZTES WORT der letzten Zeile (lowercase, satzzeichen-bereinigt).
    Das ist das Wort, auf das der SEX-CAP prueft (= die Pointe im Paarreim)."""
    if not spruch:
        return ""
    zeilen = [z.strip() for z in spruch.replace("\\n", "\n").splitlines() if z.strip()]
    if not zeilen:
        return ""
    tokens = zeilen[-1].split()
    if not tokens:
        return ""
    return tokens[-1].strip(".,!?;:\"'").lower()


def apply_sex_cap(candidates_list):
    """SEX-CAP: waehlt aus den Kandidaten den mit hoechstem judge_score, dessen
    Schlusswort NICHT in SEX_BLOCKLIST steht. Falls ALLE geblockt -> hoechsten
    ueberhaupt und sex_capped=True.

    Gibt (winner_dict, sex_capped_bool, stats_dict).
    """
    if not candidates_list:
        return None, False, {"n_total": 0, "n_blocked": 0}

    # Mit Schlusswort anreichern + nach judge_score sortieren (absteigend)
    enriched = []
    for c in candidates_list:
        sw = (c.get("letztes_wort") or _extract_schlusswort(c.get("spruch", ""))).lower()
        c = dict(c)
        c["_schlusswort"] = sw
        c["_blocked"] = sw in SEX_BLOCKLIST
        enriched.append(c)
    enriched.sort(key=lambda c: c.get("judge_score", 0), reverse=True)

    n_total = len(enriched)
    n_blocked = sum(1 for c in enriched if c["_blocked"])

    # Erster nicht-geblockter Kandidat mit hoechstem Score
    for c in enriched:
        if not c["_blocked"]:
            return c, False, {"n_total": n_total, "n_blocked": n_blocked,
                              "blocked_woerter": [c["_schlusswort"] for c in enriched if c["_blocked"]]}

    # Alle geblockt -> hoechsten ueberhaupt
    winner = enriched[0] if enriched else None
    return winner, True, {"n_total": n_total, "n_blocked": n_blocked,
                          "blocked_woerter": [c["_schlusswort"] for c in enriched if c["_blocked"]]}


# ── validate_vierzeiler: lokale Funktion (validate_spruch NICHT geaendert) ─
# Spec: "4 Zeilen verlangen und die bestehende Reim-Primitive (die
# validate_spruch intern fuer EIN Reimpaar nutzt) auf (Z1,Z2) UND (Z3,Z4)
# anwenden. Nur Kandidaten behalten, die das bestehen."

def validate_vierzeiler(spruch, klang_gruppen, reim_strenge=REIM_STRENGE):
    """Prueft einen 4-Zeiler auf AABB-Reimstruktur mittels _check_reimpaar.

    Gibt (True, None) bei PASS bzw. (False, reason_str) bei REJECT.
    Nutzt dieselbe Reim-Primitive wie validate_spruch (Paarreim-Branche,
    generator.py:2458-2470), OHNE validate_spruch zu aendern oder deren
    kausal-Hard-Reject zu uebernehmen.
    """
    def _norm(w):
        import unicodedata
        return unicodedata.normalize("NFKC", str(w)).lower().strip(".,!?;:\"'")

    if not spruch:
        return False, "leer"
    zeilen = [z.strip() for z in spruch.replace("\\n", "\n").splitlines() if z.strip()]
    if len(zeilen) != 4:
        return False, "nicht 4 zeilen (" + str(len(zeilen)) + ")"
    ende = []
    for z in zeilen:
        toks = z.split()
        if not toks:
            return False, "leere zeile"
        ende.append(_norm(toks[-1]))
    if len(ende) != 4:
        return False, "ende-extraktion fehlgeschlagen"

    # identische Reime innerhalb eines Paares abfangen
    if ende[0] == ende[1]:
        return False, "identisch: zeile 1/2 (" + ende[0] + ")"
    if ende[2] == ende[3]:
        return False, "identisch: zeile 3/4 (" + ende[2] + ")"

    ipa_map = gen._build_ipa_map(klang_gruppen)

    # Gruppe A (Z1/Z2)
    gA = set()
    if klang_gruppen:
        gA = klang_gruppen[0].get("partner", set()) | {klang_gruppen[0].get("seed", "").lower()}
    ok1, reason1 = gen._check_reimpaar(ende[0], ende[1], ipa_map, "zeile 1/2",
                                       gruppe_partner=gA, reim_strenge=reim_strenge)
    if not ok1:
        return False, reason1

    # Gruppe B (Z3/Z4)
    gB = set()
    if klang_gruppen and len(klang_gruppen) > 1:
        gB = klang_gruppen[1].get("partner", set()) | {klang_gruppen[1].get("seed", "").lower()}
    ok2, reason2 = gen._check_reimpaar(ende[2], ende[3], ipa_map, "zeile 3/4",
                                       gruppe_partner=gB, reim_strenge=reim_strenge)
    if not ok2:
        return False, reason2

    return True, None


# ── 6 Seeds deterministisch ziehen (mit Haeufigkeits-Filter) ────────────────

def draw_seeds():
    """Zieht 6 Seeds via random.Random('42:R1b-seeds'). Filter: nur Seeds
    zulassen, deren Reimgruppe bei MAX_HAEUFIGKEIT=5 noch >=3 haeufige
    Reimpartner hat (sonst naechsten Seed ziehen).

    Gibt (seeds_list, seed_reimpartner_map)."""
    data = json.loads(SEEDS_JSON.read_text(encoding="utf-8"))
    pool = data["seeds"]
    r = random.Random("{}:R1b-seeds".format(RUN_SEED))
    pool_shuffled = list(pool)
    r.shuffle(pool_shuffled)

    orig_max = gen.MAX_HAEUFIGKEIT
    gen.MAX_HAEUFIGKEIT = 5
    reset_variance_state()

    chosen = []
    seed_reimpartner = {}
    for cand in pool_shuffled:
        if len(chosen) >= N_SEEDS:
            break
        try:
            filter_rnd = random.Random("{}:R1b-filter:{}".format(RUN_SEED, cand))
            gruppen = gen._pick_seed_v2(filter_rnd, fmt="AA-2", thema=cand,
                                        use_embeddings=True)
        except Exception as e:
            print("  [skip] Seed={!r}: _pick_seed_v2-Exception: {}".format(cand, str(e)[:80]))
            continue
        n_woerter = sum(len(g.get("woerter", [])) for g in gruppen) if gruppen else 0
        if n_woerter >= 3:
            chosen.append(cand)
            seed_reimpartner[cand] = [g.get("woerter", []) for g in gruppen]
        else:
            print("  [skip] Seed={!r}: nur {} Woerter bei MAX_HAEUFIGKEIT=5".format(cand, n_woerter))

    gen.MAX_HAEUFIGKEIT = orig_max
    reset_variance_state()

    assert len(chosen) == N_SEEDS, (
        "Nur {} Seeds mit >=3 haeufigen Partnern gefunden (brauche {})".format(
            len(chosen), N_SEEDS))
    return chosen, seed_reimpartner


# ── Inkrementelles Speichern (Crash-Safe via tmp + replace) ─────────────────

def save_raw(raw):
    tmp = RAW_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(RAW_PATH)


# ── Arme ────────────────────────────────────────────────────────────────────
# Jeder Arm definiert: System-Prompt, mode (short/long), fmt_request,
# MAX_HAEUFIGKEIT, use_embeddings und ob Vierzeiler-Validierung greift.

ARMS = [
    {"id": "A", "name": "Kontrolle (SYSTEM_PROMPT, MAX_HAEUF=18, AA-2)",
     "prompt": gen.SYSTEM_PROMPT, "mode": "short", "fmt": "AA-2",
     "max_haeuf": 18, "use_emb": gen.USE_EMBEDDINGS_DEFAULT, "vierzeiler": False},
    {"id": "B", "name": "V4_ZWEI (Charge-2, MAX_HAEUF=5, AA-2)",
     "prompt": gen.SYSTEM_PROMPT_V4_ZWEI, "mode": "short", "fmt": "AA-2",
     "max_haeuf": 5, "use_emb": True, "vierzeiler": False},
    {"id": "C", "name": "V4_VIER (Charge-2 Vierzeiler, MAX_HAEUF=5, AABB-4)",
     "prompt": gen.SYSTEM_PROMPT_V4_VIER, "mode": "long", "fmt": "AABB-4",
     "max_haeuf": 5, "use_emb": True, "vierzeiler": True},
]

# Defense: Assert dass alle V4-Konstanten existieren
for _arm in ARMS:
    assert _arm["prompt"], "System-Prompt fuer Arm {} fehlt!".format(_arm["id"])
assert gen.SYSTEM_PROMPT_V4_ZWEI != gen.SYSTEM_PROMPT, "V4_ZWEI == Default"
assert gen.SYSTEM_PROMPT_V4_VIER != gen.SYSTEM_PROMPT, "V4_VIER == Default"
assert gen.SYSTEM_PROMPT_V4_ZWEI != gen.SYSTEM_PROMPT_V4_VIER, "V4_ZWEI == V4_VIER"
assert "FEW-SHOT (Charge-2" in gen.FEWSHOT_V4, "FEWSHOT_V4 fehlt Charge-2-Block"


# ── Hauptlauf ───────────────────────────────────────────────────────────────

def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    seeds, seed_reimpartner = draw_seeds()
    print("=" * 72)
    print("R.1b A/B-Lauf | RUN_SEED={} | {} Seeds (gezogen via Random('{}:R1b-seeds'),"
          " Filter: >=3 haeufige Partner bei MAX_HAEUFIGKEIT=5)".format(
              RUN_SEED, N_SEEDS, RUN_SEED))
    for i, s in enumerate(seeds, 1):
        print("  {}. {!r}  Reimpartner(haeufig): {}".format(
            i, s, seed_reimpartner.get(s, [])))
    print("Arme:")
    for a in ARMS:
        print("  {} = {}".format(a["id"], a["name"]))
    print("=" * 72)

    raw = {
        "meta": {
            "auftrag": "R.1b",
            "ziel": "Charge-2-Stil (V4) vs. Status quo. Hebel: haeufige Woerter, "
                    "Ueberraschung statt Schock, Vierzeiler-Arm, SEX-CAP.",
            "run_seed": RUN_SEED,
            "n_seeds": N_SEEDS,
            "candidates": CANDIDATES,
            "min_score": MIN_SCORE,
            "model": MODEL,
            "judge_model": JUDGE_MODEL,
            "derbheit": DERBHEIT,
            "reim_strenge": REIM_STRENGE,
            "arms": {a["id"]: a["name"] for a in ARMS},
            "sex_blocklist": sorted(SEX_BLOCKLIST),
        },
        "seeds": seeds,
        "seed_reimpartner": seed_reimpartner,
        "results": {},           # results[seed][arm_id] = {...}
        "klanggruppen_log": {},  # klanggruppen_log[seed][arm_id] = [[labels], ...]
    }
    save_raw(raw)

    _install_klang_recorder()
    _install_judge_recorder()
    _orig_default_prompt = gen.SYSTEM_PROMPT
    _orig_default_haeuf = gen.MAX_HAEUFIGKEIT

    t_start = time.time()
    n_done = 0
    n_total_runs = len(seeds) * len(ARMS)

    for seed in seeds:
        raw["results"][seed] = {}
        raw["klanggruppen_log"][seed] = {}

        for arm in ARMS:
            n_done += 1
            arm_id = arm["id"]
            print("\n[{:02d}/{}] Seed={!r} Arm={} ({})".format(
                n_done, n_total_runs, seed, arm_id, arm["name"]))

            global _KLANG_LOG, _JUDGE_POOL_LOG
            _KLANG_LOG.clear()
            _JUDGE_POOL_LOG.clear()

            # ── Pro-Arm-Setup ──
            gen.SYSTEM_PROMPT = arm["prompt"]
            gen.MAX_HAEUFIGKEIT = arm["max_haeuf"]
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
                    mode=arm["mode"],
                    candidates=CANDIDATES,
                    min_score=MIN_SCORE,
                    model=MODEL,
                    judge_model=JUDGE_MODEL,
                    thema=seed,
                    derbheit=DERBHEIT,
                    reim_strenge=REIM_STRENGE,
                    fmt_request=arm["fmt"],
                    use_embeddings=arm["use_emb"],
                    rnd=rnd,
                    no_fallback=True,
                )
            except Exception as e:
                traceback.print_exc()
                best = {"ok": False,
                        "error": "Exception: {}".format(e)[:200],
                        "spruch": "", "reimwoerter": [], "klang_gruppen": [],
                        "letztes_wort": ""}
            dt = time.time() - t0

            # ── Judge-Pool abholen (die ~8 Kandidaten + scores) ──
            judge_record = _JUDGE_POOL_LOG[-1] if _JUDGE_POOL_LOG else None
            pool_cands = judge_record["pool"] if judge_record else []
            judge_best_index = judge_record["best_index"] if judge_record else 0
            judge_begruendung = judge_record["begruendung"] if judge_record else ""

            # ── Arm C: Vierzeiler-Filter auf den Pool ──
            vierzeiler_stats = {"active": arm["vierzeiler"], "n_rejected": 0, "reasons": []}
            if arm["vierzeiler"]:
                kept = []
                for c in pool_cands:
                    ok_v, reason_v = validate_vierzeiler(
                        c.get("spruch", ""), c.get("klang_gruppen", []))
                    if ok_v:
                        kept.append(c)
                    else:
                        vierzeiler_stats["n_rejected"] += 1
                        vierzeiler_stats["reasons"].append(reason_v)
                if kept:
                    pool_cands = kept
                else:
                    print("  [warn] Arm C: ALLE Kandidaten fiel(en) durch "
                          "validate_vierzeiler — nutze unfiltered fuer SEX-CAP")

            # ── SEX-CAP auf den (ggf. gefilterten) Pool ──
            winner, sex_capped, sex_stats = apply_sex_cap(pool_cands)

            # Sieger bestimmen
            if winner is not None:
                winner_spruch = winner.get("spruch", "")
                winner_score = winner.get("judge_score", 0.0)
                winner_self = winner.get("self_score", 0)
                winner_letztes = winner.get("letztes_wort", "") or _extract_schlusswort(winner_spruch)
                winner_reimwoerter = winner.get("reimwoerter", [])
                winner_klang = winner.get("klang_gruppen", [])
            else:
                # Fallback auf den generate_spruch_best-Sieger
                winner = best
                winner_spruch = best.get("spruch", "")
                winner_score = best.get("judge_score", best.get("self_score", 0))
                try:
                    winner_score = float(winner_score)
                except (TypeError, ValueError):
                    winner_score = 0.0
                winner_self = best.get("self_score", best.get("score", 0))
                winner_letztes = best.get("letztes_wort", "") or _extract_schlusswort(winner_spruch)
                winner_reimwoerter = best.get("reimwoerter", [])
                winner_klang = best.get("klang_gruppen", [])
                sex_capped = False
                sex_stats = {"n_total": 0, "n_blocked": 0, "blocked_woerter": []}

            try:
                winner_score_f = float(winner_score)
            except (TypeError, ValueError):
                winner_score_f = 0.0

            entry = {
                "arm": arm_id,
                "arm_name": arm["name"],
                "seed": seed,
                "ok": bool(best.get("ok", False)) or (winner is not None),
                "spruch": winner_spruch,
                "reimwoerter": winner_reimwoerter,
                "klang_gruppen": winner_klang,
                "letztes_wort": winner_letztes,
                "judge_score": winner_score_f,
                "self_score": winner_self,
                "judge_begruendung": judge_begruendung,
                "model": best.get("model", MODEL),
                "error": best.get("error", ""),
                "dauer_s": round(dt, 1),
                "kosten_usd": round(float(best.get("kosten_usd", 0.0) or 0.0), 6),
                # SEX-CAP
                "sex_capped": bool(sex_capped),
                "sex_cap_stats": sex_stats,
                # Vierzeiler-Validierung (Arm C)
                "vierzeiler_validation": vierzeiler_stats,
                # Judge-Pool (alle ~8 Kandidaten + scores, fuer Analyse)
                "judge_pool": pool_cands,
                "judge_best_index": judge_best_index,
                "judge_scores": [c.get("judge_score", 0.0) for c in pool_cands],
                # WIRKLICH angebotene Klanggruppen (Hook):
                "klanggruppen_calls": [list(x["labels"]) for x in _KLANG_LOG],
                "reimwoerter_calls": [list(x["woerter"]) for x in _KLANG_LOG],
            }
            raw["results"][seed][arm_id] = entry
            raw["klanggruppen_log"][seed][arm_id] = entry["klanggruppen_calls"]
            save_raw(raw)

            print("   ok={} | judge_score={} | dauer={:.1f}s | kosten=${:.4f}".format(
                entry["ok"], winner_score_f, dt, entry["kosten_usd"]))
            print("   Spruch: {}".format(winner_spruch[:140]))
            print("   reimwoerter: {}".format(winner_reimwoerter))
            print("   SEX-CAP: capped={} ({}/{}) | Vierz-Rejects: {}".format(
                sex_capped, sex_stats.get("n_blocked", 0),
                sex_stats.get("n_total", 0), vierzeiler_stats["n_rejected"]))
            print("   angebotene Klanggruppen ({}x): {}".format(
                len(_KLANG_LOG), entry["klanggruppen_calls"]))

    # App-Default wiederherstellen (KEINE dauerhafte Aenderung)
    gen.SYSTEM_PROMPT = _orig_default_prompt
    gen.MAX_HAEUFIGKEIT = _orig_default_haeuf

    total_dt = time.time() - t_start
    print("\n" + "=" * 72)
    print("Lauf beendet in {:.1f}s ({:.1f} min)".format(total_dt, total_dt / 60))
    print("=" * 72)

    # ── Verifikation 1: ok-Quote (Sieger validate-gruen / Output vorhanden) ──
    print("\nVERIFIKATION: Sieger mit Output")
    print("-" * 72)
    n_ok = 0
    for seed in seeds:
        for a in ARMS:
            e = raw["results"][seed][a["id"]]
            if e["spruch"] and e["spruch"] != "(kein Output)":
                n_ok += 1
            else:
                print("  FAIL: seed={!r} arm={} error={}".format(
                    seed, a["id"], e.get("error", "")))
    print("{}/{} Sieger mit Spruch-Output".format(n_ok, n_total_runs))

    # ── Verifikation 2: Arm C - Vierzeiler-Struktur (4 Zeilen) ──
    print("\nVERIFIKATION: Arm C Sieger sind 4-Zeiler")
    print("-" * 72)
    for seed in seeds:
        e = raw["results"][seed]["C"]
        spruch = e.get("spruch", "")
        n_lines = len([z for z in spruch.replace("\\n", "\n").splitlines() if z.strip()])
        print("  Seed={!r}: {} Zeilen — {}".format(seed, n_lines, spruch[:80]))

    # ── Verifikation 3: SEX-CAP-Statistik ──
    print("\nVERIFIKATION: SEX-CAP-Statistik je Arm")
    print("-" * 72)
    for a in ARMS:
        n_capped = sum(1 for seed in seeds
                       if raw["results"][seed][a["id"]].get("sex_capped"))
        n_blocked_total = sum(raw["results"][seed][a["id"]]["sex_cap_stats"].get("n_blocked", 0)
                              for seed in seeds)
        n_total_pool = sum(raw["results"][seed][a["id"]]["sex_cap_stats"].get("n_total", 0)
                           for seed in seeds)
        print("  Arm {} ({}): {}/{} Seeds komplett gekappt | "
              "{} von {} Pool-Kandidaten geblockt".format(
                  a["id"], a["name"], n_capped, len(seeds),
                  n_blocked_total, n_total_pool))

    # ── Konsolen-Summary: grok-Judge-Schnitt je Arm ──
    print("\n" + "=" * 72)
    print("JUDGE-SCHNITT JE ARM ({} als Judge)".format(JUDGE_MODEL))
    print("=" * 72)
    arm_scores = {a["id"]: [] for a in ARMS}
    for seed in seeds:
        for a in ARMS:
            sc = raw["results"][seed][a["id"]].get("judge_score")
            if isinstance(sc, (int, float)) and sc > 0:
                arm_scores[a["id"]].append(float(sc))
    for a in ARMS:
        scs = arm_scores[a["id"]]
        avg = sum(scs) / len(scs) if scs else 0.0
        print("  Arm {} ({}): avg={:.2f} (n={}/{})".format(
            a["id"], a["name"], avg, len(scs), len(seeds)))
        print("     single scores: {}".format(scs))
    baseline = (sum(arm_scores["A"]) / len(arm_scores["A"])
                if arm_scores["A"] else 0.0)
    for aid in ("B", "C"):
        scs = arm_scores[aid]
        avg = sum(scs) / len(scs) if scs else 0.0
        delta = avg - baseline
        print("  Delta Arm {} vs A: {:+.2f}".format(aid, delta))

    # ── blind_check_R1b.md schreiben ────────────────────────────────────────
    write_blind(raw, seeds)
    print("\nGeschrieben: {}".format(RAW_PATH))
    print("Geschrieben: {}".format(BLIND_PATH))


def write_blind(raw, seeds):
    """output/blind_check_R1b.md: pro Seed 3 Sieger anonym + randomisiert."""
    blind_rnd = random.Random("{}:blind".format(RUN_SEED))
    lines = []
    lines.append("# R.1b Blind-Check - Charge-2-Stil (V4) vs. Status quo")
    lines.append("")
    lines.append("{} Seeds x 3 Arme = 18 Sprueche. Pro Seed neu randomisiert "
                 "(Seed={}). KEIN Arm-Name im anonymen Teil.".format(
                     len(seeds), RUN_SEED))
    lines.append("")
    lines.append("Arme: A=Kontrolle (Default, MAX_HAEUF=18, AA-2) | "
                 "B=V4_ZWEI (Charge-2, MAX_HAEUF=5, AA-2) | "
                 "C=V4_VIER (Charge-2 Vierzeiler, MAX_HAEUF=5, AABB-4).")
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
        drei = [(a["id"], raw["results"][seed][a["id"]]) for a in ARMS]
        blind_rnd.shuffle(drei)
        for i, (arm_id, entry) in enumerate(drei):
            label = chr(65 + i)  # A, B, C (anonyme Labels, nicht Arm-IDs)
            spruch = entry["spruch"] or "(kein Output)"
            lines.append("**{})**".format(label))
            lines.append("")
            for zeile in spruch.replace("\\n", "\n").splitlines():
                if zeile.strip():
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
