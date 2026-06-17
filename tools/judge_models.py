"""tools/judge_models.py — Q.2: 7-Modell-Vergleich unter FIXEM Seed, PARALLELISIERT.

ZIEL (Q.2): Decken- und Boden-Frage sauber beantworten — alle 7 Kandidaten
unter IDENTISCHEN Reimgruppen/Woertern gegen grok-4.3 messen. Den
ungeseedeten Generator-RNG fixieren (RUN_SEED=42), damit das Delta KAUSAL
(Modell-Unterschied) statt Klang-Rauschen ist.

VORARBEIT (Pflicht, ZUERST im Report):
    (1) GLM-Routing heute: GLM_API_URL = "https://api.z.ai/api/paas/v4/chat/
        completions" (generator.py), Key aus config.json['api_key'] (bisher),
        ab Q.2 dupliziert in glm_base_url/glm_api_key fuer den 4-Zweig-
        Dispatcher. _glm_call_single() ist die Single-Modell-Variante OHNE
        Fallback-Kette (fuer kausale Deltas).
    (2) Dispatcher _llm_call: vier Zweige grok/glm/gpt/else (DeepInfra).
        no_fallback=True schaltet die DeepInfra-Fallback-Kette ab (nur das
        angeforderte Modell) — wichtig, weil sonst z.B. DeepSeek-V4-Pro
        unsichtbar auf Qwen3-235B fallen back und das Delta verschmutzt
        (wie in Q.1 passiert).
    (3) random.Random() OHNE Seed: 4 Stellen im Generator
        (generate_spruch, _generate_spruch_legacy, pick_gruppe, generate_
        spruch_best). Ab Q.2 durchreicht der Caller (hier) einen gesceeten
        rnd = random.Random(f"{RUN_SEED}:{seed}") an generate_spruch_best.
        _pick_seed_v2 hatte bereits einen rnd-Parameter.
    (4) variance_state.json (last_20_klang_gruppen etc.) wird waehrend des
        Laufs von _update_variance modifiziert -> Modell 2 saehe anderen
        penalized_klang als Modell 1. Q.2 resettet variance vor JEDEM Task.

PARALLELISIERUNG:
    ThreadPoolExecutor(max_workers=8), Tasks = 7x6 = 42 (Modell, Seed)-Paare.
    I/O-bound (API-Warten) -> kein GIL-Problem. Klanggruppen/Reimwoerter sind
    pro Seed deterministisch aus random.Random(f"{RUN_SEED}:{seed}") ->
    reihenfolge-unabhaengig. Token-Isolation pro Task via thread-local
    Monkey-Patch von generator._session_add.

GEGENPROBE (Pflicht): EIN (Modell,Seed) zusaetzlich SERIELL laufen lassen und
    bestaetigen, dass angebotene klaenge/woerter IDENTISCH zum parallelen
    Lauf -> beweist, dass Parallelitaet die Vergleichbarkeit nicht beruehrt.

ROBUSTHEIT: 402/429/Fehler -> NUR diesen Modell-Block ueberspringen, Rest zu
    Ende, Fehler im Report vermerken. KEIN Komplett-Abbruch.

CAVEATS (Q.2, RNG ist jetzt FIX):
    (1) RNG jetzt FIX (RUN_SEED=42) -> Delta ist kausal (alter Rauschen-
        Caveat erledigt).
    (2) Verbleibend: Judge = grok-4.3 -> moeglicher Selbstbias zugunsten von
        grok-4.3. Blind-Check (output/blind_check.md) = menschlicher Tiebreaker.
    (3) Token-Counts pro Task via thread-local _session_add (Gen+Judge
        kombiniert, pro Task isoliert).
"""

import json
import os
import random as _random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Pfad-Setup: Projekt-Root in sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spruch_app import generator as gen


# ── Thread-Local-Session: isoliert Token-Counts pro Task ────────────────────
# Generator._session_add schreibt normal in globales _SESSION. Bei 8 parallelen
# Tasks vermischen sich die Zaehler -> keine eindeutige Pro-Task-Zuordnung.
# Wir ueberschreiben _session_add mit einer Thread-Local-Variante: jeder Thread
# fuehrt seinen eigenen Zaehler, den run_one() vor/nach dem Task ausliest.
_TL = threading.local()


def _tl_session():
    """Liefert (und initiiert ggf.) die thread-lokale Session."""
    if not hasattr(_TL, "session"):
        _TL.session = {"prompt_tokens": 0, "completion_tokens": 0,
                       "kosten_usd": 0.0, "calls": 0,
                       "per_model": {}}
    return _TL.session


def _tl_reset():
    """Setzt die thread-lokale Session auf 0 zurueck (vor Task)."""
    _TL.session = {"prompt_tokens": 0, "completion_tokens": 0,
                   "kosten_usd": 0.0, "calls": 0, "per_model": {}}


def _patched_session_add(model, pt, ct):
    """Thread-lokaler Ersatz fuer generator._session_add.

    Macht ZWEI Dinge, damit Q.2 parallel lauffaehig UND monitobar bleibt:
    (1) Thread-lokale Zaehler (Gen+Judge-Token pro Task) -> run_one liest
        sie nach dem Task aus. Keine Vermischung bei 8 parallelen Threads.
    (2) Live-Eintrag ins globale cost_log.json fuer Monitoring. Das Original
        tut das in _session_add auch, aber _save_cost_entry liest-schreibt
        das ganze File -> bei 8 Threads Race Condition (lost updates).
        Daher eigene _COST_LOCK um den Lese-Schreib-Zyklus.
    """
    s = _tl_session()
    s["prompt_tokens"] += int(pt or 0)
    s["completion_tokens"] += int(ct or 0)
    s["calls"] += 1
    pm = s["per_model"].setdefault(model or "?",
                                   {"prompt": 0, "completion": 0, "calls": 0})
    pm["prompt"] += int(pt or 0)
    pm["completion"] += int(ct or 0)
    pm["calls"] += 1
    # Live-Monitoring: thread-sicher ins cost_log.json eintragen.
    if pt or ct:
        with _COST_LOCK:
            try:
                gen._save_cost_entry(model, int(pt or 0), int(ct or 0),
                                     gen._calc_cost(model, int(pt or 0),
                                                    int(ct or 0)))
            except Exception as _e:
                # Cost-Log darf niemals den Lauf killen
                print("  [warn] cost_log write failed: " + str(_e)[:80])


# Monkey-Patch (MUSS vor ersten generate_spruch_best-Aufruf erfolgen)
gen._session_add = _patched_session_add


# Q.2-Determinismus-Fix: _update_variance zu No-Op patchen.
# Grund: _update_variance() schreibt WAEHREND generate_spruch_best die
# akzeptierten Klanggruppen in variance_state.json. Bei parallelen Tasks +
# Retries verschmutzt das den penalized_klang-Satz fuer nachfolgende
# _pick_seed_v2-Aufrufe (Race: Thread A _update_variance schreibt, Thread B
# _load_variance liest halbfertigen State). reset_variance_state() VOR dem
# Task reicht nicht, weil _update_variance ZWISCHEN Reset und Pick feuert.
# No-Op fuer Q.2-Messlauf: variance_state.json ist nur fuer menschliche
# App-Nutzer live relevant, im Messlauf egal. Reset vor jedem Task bleibt
# als Defense-in-Depth fuer den normalen App-Betrieb nach Q.2.
def _noop_update_variance(*args, **kwargs):
    return None


gen._update_variance = _noop_update_variance


# ── Fixe Konfiguration ─────────────────────────────────────────────────────

# Q.2: 7 Modelle — Decke + Boden in einem Durchgang gegen grok-4.3.
# (model_id, label). Reihenfolge = auch Anzeige-Reihenfolge.
# Modell-IDs an echte Katalognamen angepasst (Smoke-Test 2026-06-17 alle OK).
# GLM-5.1: wird via 4-Zweig-Dispatcher an z.ai geroutet (glm_base_url/glm_api_key)
# GPT-5.4/-mini: werden via OpenAI-Endpoint geroutet (max_completion_tokens)
# DeepInfra-Org-Praefix: anthropic/*, Qwen/*
MODELS = [
    ("grok-4.3",                           "grok-4.3"),         # Baseline (= Judge)
    ("anthropic/claude-opus-4-8",          "Claude-Opus-4.8"),  # Decke (Anthropic)
    ("gpt-5.4",                            "GPT-5.4"),          # Decke (OpenAI)
    ("gpt-5.4-mini",                       "GPT-5.4-mini"),     # Mid (OpenAI)
    ("glm-5.1",                            "GLM-5.1"),          # Mid (z.ai)
    ("Qwen/Qwen3-235B-A22B-Instruct-2507", "Qwen3-235B-2507"),  # Boden Budget
    ("Qwen/Qwen3-32B",                     "Qwen3-32B"),        # Boden Mini
]

# Q.2 Methodik-Fix: fixer Seed -> deterministische Klanggruppen + Wort-Sampling.
# Pro Seed-Wort s: rnd = random.Random(f"{RUN_SEED}:{s}"). Alle 7 Modelle
# ziehen damit EXAKT dieselben angebotenen Klanggruppen/Reimwoerter -> das
# Delta ist reiner Modelleffekt.
RUN_SEED = 42

# 6 fixierte thema-Seeds (identisch zu Q.1 / judge_ab.py fuer Vergleichbarkeit)
SEEDS = ["Bauer", "Liebe", "Geld", "Tod", "Kind", "Frau"]

# fmt pro Seed (gleich fuer alle Modelle). Abwechselnd fuer Streuung.
FMTS = ["gemischt", "AABB-4", "AA-2", "gemischt", "AABB-4", "AA-2"]

# Identische Settings fuer alle Modelle (wie 7b04e83, UNVERAENDERT)
CANDIDATES   = 8
MIN_SCORE    = 4
DERBHEIT     = "derb"
REIM_STRENGE = "DB-streng"
JUDGE_MODEL  = "grok-4.3"   # KONSTANT fuer alle Generatoren (Vergleichbarkeit)
USE_EMBEDDINGS = False      # P-Entscheid: Embeddings bleiben AUS

# Parallelitaet
MAX_WORKERS = 8             # respektiert xAI/OpenAI/GLM-Rate-Limits; DI: 200 parallel
MAX_RETRIES = 4             # 429/5xx Hard-Skip-Schwelle pro Task
BACKOFF_BASE = 2.0          # exponentieller Backoff: 2^k Sekunden

# Kosten-Annahmen (USD pro 1M Tokens; Input/Output).
# Quelle: Hersteller-/DeepInfra-Pricing Stand 2026-06.
# GPT-5.4/-mini: OpenAI-Listing ($2.50/$15 und $0.75/$4.50)
# Claude-Opus-4.8: DeepInfra spiegelt Anthropic-Pricing ($15/$75)
# GLM-5.1: Zhipu-Standard ~$0.90/$0.90 (Schaetzung, GLM-5-Preisklasse)
# Qwen3-235B-2507: DeepInfra-Standard 235B-MoE ~$0.30/$0.60
# Qwen3-32B: DeepInfra-Standard 32B ~$0.05/$0.10
COST_PER_1M = {
    "grok-4.3":                           {"input": 1.25,  "output": 2.50},
    "anthropic/claude-opus-4-8":          {"input": 15.0,  "output": 75.0},
    "gpt-5.4":                            {"input": 2.50,  "output": 15.0},
    "gpt-5.4-mini":                       {"input": 0.75,  "output": 4.50},
    "glm-5.1":                            {"input": 0.90,  "output": 0.90},
    "Qwen/Qwen3-235B-A22B-Instruct-2507": {"input": 0.30,  "output": 0.60},
    "Qwen/Qwen3-32B":                     {"input": 0.05,  "output": 0.10},
}

# Pfade
REPORT_PATH      = ROOT / "output" / "judge_models_report.json"
BLIND_CHECK_PATH = ROOT / "output" / "blind_check.md"
VARIANCE_PATH    = ROOT / "output" / "variance_state.json"

# Lock fuer Thread-sicheres results-Append + _save_report
_SAVE_LOCK = threading.Lock()

# Lock fuer thread-sicheres cost_log.json (read-append-write Zyklus in _patched_session_add)
_COST_LOCK = threading.Lock()


# ── Hilfsfunktionen ────────────────────────────────────────────────────────

def reset_variance_state():
    """Setzt variance_state.json auf einen leeren State zurueck.

    Warum: _update_variance() schiebt waehrend eines Tasks die generierten
    Klanggruppen in last_20_klang_gruppen -> beim NAECHSTEN Task waere der
    penalized_klang-Satz anders. Q.2 will, dass Modell 2 denselben baseline-
    State sieht wie Modell 1. Reset vor JEDEM Task.
    """
    leer = {"last_10_personas": [], "last_10_settings": [],
            "last_20_klang_gruppen": [], "last_20_reimwoerter": []}
    try:
        VARIANCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(VARIANCE_PATH, "w", encoding="utf-8") as f:
            json.dump(leer, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("  [warn] variance reset failed: " + str(e))


def extract_reimwoerter(result):
    """Extrahiert die gesampelten Reimwoerter aus dem Result."""
    rw = result.get("reimwoerter") or []
    if rw:
        return [str(w).lower() for w in rw]
    cast = result.get("cast") or []
    return [str(c).lower() for c in cast if c]


def extract_klang(result):
    """Extrahiert die angebotenen Klanggruppen aus dem Result."""
    k = result.get("klang_gruppen") or []
    return [str(x) for x in k] if isinstance(k, list) else [str(k)]


def run_one(model_id, label, seed, fmt):
    """Fuehrt EINE Generierung durch. Gibt Resultat-Dict zurueck.

    Q.2:
    - rnd = random.Random(f"{RUN_SEED}:{seed}") -> deterministische
      Klanggruppen + Wort-Sampling. Identisch fuer alle 7 Modelle pro Seed.
    - no_fallback=True -> Provider-Fallback-Kette AUS (kausale Deltas).
    - conv_id pro Task -> Grok-Prompt-Caching bleibt aktiv, aber isoliert.
    - Vor Task: _tl_reset() + reset_variance_state(). Nach Task: thread-local
      auslesen -> exakte Gen+Judge-Tokens fuer diesen Task.
    """
    _tl_reset()
    reset_variance_state()

    # Seeded RNG pro Seed-Wort -> identische Gruppen/Worte fuer alle Modelle
    rnd = _random.Random("{}:{}".format(RUN_SEED, seed))

    print("\n[" + label + " | " + seed + " | " + fmt + "] start")
    t0 = time.time()
    err_msg = None
    result = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # Variance-Verschmutzung durch vorherige Versuche verhindern
            reset_variance_state()
            # Bei Retry: neuer seeded rnd (Seed-Wort aendert sich nicht ->
            # dieselbe Sequenz; OK weil wir dasselbe wie beim Erstversuch wollen)
            rnd_call = _random.Random("{}:{}".format(RUN_SEED, seed))
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
                rnd=rnd_call,
                no_fallback=True,
            )
            # Erfolg -> raus
            if result.get("ok") or result.get("spruch"):
                break
            # ok=False ohne Spruch -> wahrscheinlich API-Fehler im Generator
            err_msg = result.get("error", "no output")
            print("  [warn] Versuch " + str(attempt) + ": ok=False (" + str(err_msg)[:80] + ")")
        except Exception as e:
            err_msg = str(e)
            print("  [warn] Versuch " + str(attempt) + " Exception: " + err_msg[:120])

        # Backoff vor naechstem Retry (außer beim letzten Versuch)
        if attempt < MAX_RETRIES:
            w = BACKOFF_BASE ** attempt
            print("  Retry in " + str(w) + "s ...")
            time.sleep(w)

    elapsed = time.time() - t0

    # Thread-lokale Tokens fuer diesen Task auslesen
    tl = _tl_session()
    pt = int(tl.get("prompt_tokens", 0))
    ct = int(tl.get("completion_tokens", 0))
    per_model = dict(tl.get("per_model", {}))

    if result is None:
        result = {"ok": False, "error": err_msg or "unknown",
                  "spruch": "", "reimwoerter": [], "klang_gruppen": []}

    ok = bool(result.get("ok", False))
    judge_score = result.get("judge_score")
    try:
        judge_score = float(judge_score) if judge_score is not None else None
    except (TypeError, ValueError):
        judge_score = None
    reimwoerter = extract_reimwoerter(result)
    spruch = (result.get("spruch") or result.get("letzter_spruch") or "").strip()
    klang = extract_klang(result)
    err = result.get("error") or err_msg

    print("[" + label + " | " + seed + "] done: ok=" + str(ok)
          + " score=" + str(judge_score) + " p/c=" + str(pt) + "/" + str(ct)
          + " d=" + str(round(elapsed, 1)) + "s")

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
        "per_model_tokens": per_model,
        "skipped": (not ok),
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
    A) ... / B) ... / ... / G) ohne Modell-Namen aufgefuehrt. Die
    Zuordnung (Buchstabe -> Modell) steht separat am Ende.
    """
    lines = []
    lines.append("# Blind-Check - Modell-Vergleich (anonym, Q.2)")
    lines.append("")
    lines.append("Sprueche pro Seed zufaellig durchmischt, OHNE Modell-Label.")
    lines.append("Judge-Score bewusst ENTFERNT - reine menschliche Bewertung.")
    lines.append("Aufloesung (welcher Buchstabe = welches Modell) am Ende.")
    lines.append("")
    lines.append("---")
    lines.append("")

    aufloesung = []
    rng = _random.Random(12345)  # fester Seed fuer Reproduzierbarkeit der Mischung
    for seed, rows in rows_per_seed.items():
        if not rows:
            continue
        fmt = rows[0].get("fmt", "?")
        lines.append("## Seed: " + seed + "  (fmt=" + fmt + ")")
        lines.append("")
        order = list(range(len(rows)))
        rng.shuffle(order)
        buchstaben = [chr(ord("A") + i) for i in range(len(rows))]
        mapping = {}
        for slot_idx, row_idx in enumerate(order):
            buchstabe = buchstaben[slot_idx]
            spruch = rows[row_idx].get("spruch") or "(kein Spruch)"
            spruch_einzeiler = spruch.replace("\n", " / ")
            lines.append("**" + buchstabe + ")** " + spruch_einzeiler)
            lines.append("")
            mapping[buchstabe] = rows[row_idx].get("label", "?")
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
    print("\nBlind-Check gespeichert: " + str(BLIND_CHECK_PATH))


def _auswertung(results, model_labels_done, overall_elapsed, partial=False,
                gegenprobe_ok=None, seed_verifikation=None):
    """Berechnet Auswertung fuer bereits fertige Modelle."""
    baseline_lbl = model_labels_done[0] if model_labels_done else None

    sum_per_model = {lbl: 0.0 for lbl in model_labels_done}
    n_per_model = {lbl: 0 for lbl in model_labels_done}
    tokens_per_model = {lbl: {"prompt": 0, "completion": 0, "gesamt": 0}
                        for lbl in model_labels_done}
    cost_per_model = {lbl: 0.0 for lbl in model_labels_done}
    skipped = {lbl: [] for lbl in model_labels_done}

    for seed in SEEDS:
        for lbl in model_labels_done:
            if seed not in results[lbl]:
                continue
            res = results[lbl][seed]
            js = res.get("judge_score")
            if js is not None:
                sum_per_model[lbl] += js
                n_per_model[lbl] += 1
            t = res["tokens"]
            tokens_per_model[lbl]["prompt"] += t["prompt"]
            tokens_per_model[lbl]["completion"] += t["completion"]
            tokens_per_model[lbl]["gesamt"] += t["gesamt"]
            cost_per_model[lbl] += calc_cost(res["model_id"],
                                             t["prompt"], t["completion"])
            if res.get("skipped"):
                skipped[lbl].append(seed + ": " + str(res.get("error", "?"))[:120])

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
        "skipped": skipped,
        "gegenprobe_ok": gegenprobe_ok,
        "seed_verifikation": seed_verifikation,
    }


VORARBEIT_TEXT = (
    "Q.2 7-Modell-Vergleich unter RUN_SEED=42. Vorarbeit (live 2026-06-17):\n"
    "1. GLM-Routing heute: GLM_API_URL=https://api.z.ai/api/paas/v4/chat/completions, "
    "Key bisher config.json['api_key'], ab Q.2 dupliziert als glm_base_url/glm_api_key. "
    "_glm_call_single() ist die Single-Modell-Variante OHNE Fallback-Kette.\n"
    "2. Dispatcher _llm_call: vier Zweige grok/glm/gpt/else(DeepInfra). "
    "no_fallback=True schaltet DeepInfra-Fallback-Kette ab (nur das angeforderte "
    "Modell) -> kausale Deltas (Q.1 hatte DeepSeek-V4-Pro->Qwen3-235B-Verunreinigung).\n"
    "3. random.Random() OHNE Seed: 4 Stellen (generate_spruch, _generate_spruch_legacy, "
    "pick_gruppe, generate_spruch_best). Q.2 reicht rnd=random.Random(f'{RUN_SEED}:{seed}') "
    "durch -> alle 7 Modelle ziehen pro Seed EXAKT dieselben Klanggruppen/Reimwoerter.\n"
    "4. variance_state.json (last_20_klang_gruppen) wird waehrend Laufs modifiziert -> "
    "Q.2 resettet variance vor JEDEM Task.\n"
    "5. OpenAI-Eigenheit: gpt-5.x lehnt max_tokens ab, braucht max_completion_tokens "
    "(im _openai_call umgesetzt)."
)

FOOTER_TEXT = (
    "RUN_SEED=42, identische Gruppen pro Seed -> Deltas kausal. "
    "Judge = grok-4.3 (konstant) -> moeglicher Selbstbias, Blind-Check = Tiebreaker."
)


def _save_report(results, model_labels_done, overall_elapsed, partial=False,
                 gegenprobe_ok=None, seed_verifikation=None):
    """Schreibt Report-JSON thread-sicher (atomar via tmp+replace).

    Wird nach jedem fertigen (Modell, Seed)-Task aufgerufen.
    """
    with _SAVE_LOCK:
        ausw = _auswertung(results, model_labels_done, overall_elapsed,
                           partial=partial,
                           gegenprobe_ok=gegenprobe_ok,
                           seed_verifikation=seed_verifikation)
        report = {
            "version": "Q.2",
            "run_seed": RUN_SEED,
            "footer": FOOTER_TEXT,
            "vorarbeit": VORARBEIT_TEXT,
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
                "no_fallback": True,
                "max_workers": MAX_WORKERS,
                "max_retries": MAX_RETRIES,
                "cost_per_1m": COST_PER_1M,
            },
            "results": {lbl: {s: results[lbl][s] for s in SEEDS
                              if s in results.get(lbl, {})}
                        for lbl in model_labels_done},
            "auswertung": ausw,
        }
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = REPORT_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        tmp.replace(REPORT_PATH)


# ── GEGENPROBE (Pflicht) ───────────────────────────────────────────────────

def gegenprobe():
    """GEGENPROBE (Pflicht im Report): EIN (Modell, Seed) SERIELL laufen lassen
    und bestaetigen, dass die angebotenen klang_gruppen + reimwoerter IDENTISCH
    zur spaeteren parallelen Ausfuehrung sind. Beweist: Parallelitaet aendert
    die Vergleichbarkeit nicht (denn die Werte sind rnd-deterministisch).

    Wir nehmen (grok-4.3, 'Bauer') und vergleichen klang+reimwoerter mit dem
    Ergebnis des parallelen Hauptlaufs (spaeter in main() gematcht).
    """
    print("\n" + "#" * 72)
    print("# GEGENPROBE (Pflicht): (grok-4.3, Bauer) SERIELL")
    print("# -> klang_gruppen/reimwoerter muessen mit parallelem Lauf identisch sein")
    print("#" * 72)
    res = run_one("grok-4.3", "grok-4.3", "Bauer", "gemischt")
    print("# GEGENPROBE klang   : " + str(res.get("klang_gruppen", [])))
    print("# GEGENPROBE reim    : " + str(res.get("reimwoerter", [])))
    return res


def match_gegenprobe(seriell_res, results):
    """Vergleicht die serielle GEGENPROBE mit dem parallelen Lauf fuer
    (grok-4.3, Bauer) und liefert True/False + Details."""
    par = results.get("grok-4.3", {}).get("Bauer")
    if not par:
        return False, "kein paralleler Eintrag fuer grok-4.3/Bauer"
    sk = seriell_res.get("klang_gruppen", [])
    pk = par.get("klang_gruppen", [])
    sr = seriell_res.get("reimwoerter", [])
    pr = par.get("reimwoerter", [])
    klang_match = (sk == pk)
    reim_match = (sr == pr)
    ok = klang_match and reim_match
    detail = {
        "klang_match": klang_match,
        "reim_match": reim_match,
        "seriell_klang": sk,
        "parallel_klang": pk,
        "seriell_reim": sr,
        "parallel_reim": pr,
    }
    return ok, detail


def verify_seed_determinism(results):
    """Verifiziert fuer jeden Seed, dass alle 7 Modelle EXAKT dieselben
    klang_gruppen UND reimwoerter gezogen haben. Liefert {seed: {ok, ...}}."""
    out = {}
    for seed in SEEDS:
        klangs = []
        reims = []
        for _, lbl in MODELS:
            res = results.get(lbl, {}).get(seed)
            if not res:
                continue
            klangs.append((lbl, res.get("klang_gruppen", [])))
            reims.append((lbl, res.get("reimwoerter", [])))
        if not klangs:
            out[seed] = {"ok": False, "grund": "keine Daten"}
            continue
        ref_klang = klangs[0][1]
        ref_reim = reims[0][1]
        klang_ok = all(k == ref_klang for _, k in klangs)
        reim_ok = all(r == ref_reim for _, r in reims)
        out[seed] = {
            "ok": klang_ok and reim_ok,
            "klang_ok": klang_ok,
            "reim_ok": reim_ok,
            "ref_klang": ref_klang,
            "ref_reim": ref_reim,
        }
    return out


# ── Hauptprogramm ──────────────────────────────────────────────────────────

def main():
    print("#" * 72)
    print("# judge_models.py Q.2 - 7-Modell-Vergleich (FIXER Seed, PARALLEL)")
    print("#")
    print("# RUN_SEED   : " + str(RUN_SEED))
    print("# Modelle    : " + ", ".join(lbl for _, lbl in MODELS))
    print("# Seeds      : " + ", ".join(SEEDS))
    print("# fmts       : " + ", ".join(FMTS))
    print("# candidates : " + str(CANDIDATES) + " | min_score: " + str(MIN_SCORE))
    print("# derbheit   : " + DERBHEIT + " | reim_strenge: " + REIM_STRENGE)
    print("# judge      : " + JUDGE_MODEL + " (KONSTANT)")
    print("# embeddings : " + str(USE_EMBEDDINGS) + " | no_fallback: True")
    print("# parallel   : max_workers=" + str(MAX_WORKERS)
          + ", retries=" + str(MAX_RETRIES))
    print("#" * 72)

    # API-Keys pruefen
    glm_key = gen._read_glm_api_key()
    grok_key = gen._read_grok_api_key()
    di_key = gen._read_deepinfra_api_key()
    oai_key = gen._read_openai_api_key()
    print("API-Keys: GLM=" + ("ok" if glm_key else "FEHLT")
          + " | Grok=" + ("ok" if grok_key else "FEHLT")
          + " | DeepInfra=" + ("ok" if di_key else "FEHLT")
          + " | OpenAI=" + ("ok" if oai_key else "FEHLT"))
    if not (glm_key and grok_key and di_key and oai_key):
        print("ABBRUCH: mind. ein API-Key fehlt.")
        sys.exit(1)
    print()

    # 1) GEGENPROBE (seriell, vor dem Hauptlauf) — NUR Vergleichswert merken,
    #    NICHT in results ablegen (sonst wuerde der Hauptlauf diesen Task
    #    skippen und es gaebe keinen parallelen Vergleichswert).
    gp_res = gegenprobe()

    # 2) Hauptlauf: alle 42 (Modell, Seed)-Paare parallel. grok-4.3/Bauer
    #    laeuft hier NOCHMAL parallel, damit die GEGENPROBE gematcht werden kann.
    tasks = []
    for model_id, label in MODELS:
        for i, seed in enumerate(SEEDS):
            tasks.append((model_id, label, seed, FMTS[i]))

    print("\n" + "#" * 72)
    print("# HAUPTLAUF: " + str(len(tasks)) + " Tasks parallel (max_workers="
          + str(MAX_WORKERS) + ")")
    print("#" * 72)

    overall_t0 = time.time()
    results = {label: {} for _, label in MODELS}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        # future -> key Mapping, damit as_completed korrekt aufgerufen wird
        # (as_completed braucht Future-Objekte, NICHT dict-Keys).
        future_to_key = {}
        for (model_id, label, seed, fmt) in tasks:
            f = ex.submit(run_one, model_id, label, seed, fmt)
            future_to_key[f] = (model_id, label, seed)

        for fut in as_completed(future_to_key):
            (model_id, label, seed) = future_to_key[fut]
            try:
                res = fut.result()
            except Exception as e:
                print("  [FEHLER] Task " + label + "/" + seed + " crashed: " + str(e)[:120])
                res = {
                    "model_id": model_id, "label": label, "seed": seed,
                    "fmt": FMTS[SEEDS.index(seed)], "ok": False,
                    "error": "crash: " + str(e)[:200],
                    "judge_score": None, "reimwoerter": [], "klang_gruppen": [],
                    "spruch": "", "judge_begruendung": "",
                    "elapsed_s": 0.0,
                    "tokens": {"prompt": 0, "completion": 0, "gesamt": 0},
                    "per_model_tokens": {}, "skipped": True,
                }
            with _SAVE_LOCK:
                results[label][seed] = res
            # inkrementeller Speicher nach jedem fertigen Task
            done_labels = [lbl for _, lbl in MODELS
                           if len(results.get(lbl, {})) >= 1]
            total_done = sum(len(results.get(lbl, {})) for _, lbl in MODELS)
            total_all = len(MODELS) * len(SEEDS)
            _save_report(results, done_labels, time.time() - overall_t0,
                         partial=(total_done < total_all))
            print("[inkrementell] " + str(total_done) + "/" + str(total_all)
                  + " Tasks gespeichert")

    # 3) GEGENPROBE matchen
    gp_ok, gp_detail = match_gegenprobe(gp_res, results)
    print("\nGEGENPROBE Match: " + ("OK" if gp_ok else "MISMATCH"))
    if not gp_ok:
        print("  Detail: " + str(gp_detail)[:300])

    # 4) Seed-Determinismus verifizieren (alle 7 Modelle pro Seed = identisch?)
    seed_verif = verify_seed_determinism(results)
    bad = [s for s, v in seed_verif.items() if not v.get("ok")]
    print("\nSeed-Determinismus: " + ("OK alle" if not bad
          else ("MISMATCH in: " + ", ".join(bad))))

    overall_elapsed = time.time() - overall_t0

    # 5) Auswertung + Konsole
    print("\n\n" + "=" * 72)
    print("  AUSWERTUNG (Q.2)")
    print("=" * 72)

    model_labels = [lbl for _, lbl in MODELS]
    ausw = _auswertung(results, model_labels, overall_elapsed, partial=False,
                       gegenprobe_ok=gp_ok, seed_verifikation=seed_verif)
    avg_per_model = {lbl: ausw["avg_per_model"][lbl] for lbl in model_labels}
    tokens_per_model = ausw["tokens_per_model"]
    cost_per_model = ausw["cost_per_model"]
    cost_per_point = ausw["cost_per_point"]
    ranking = ausw["ranking"]
    baseline_lbl = model_labels[0]

    # Tabelle Seed | fmt | Judge je Modell
    header = "Seed      | fmt     |"
    for lbl in model_labels:
        header += " " + lbl[:13].ljust(13) + " |"
    print("\n" + header)
    print("-" * len(header))
    for i, seed in enumerate(SEEDS):
        fmt = FMTS[i]
        row = "{:9s} | {:7s} |".format(seed, fmt)
        for lbl in model_labels:
            res = results[lbl].get(seed, {})
            js = res.get("judge_score")
            cell = ("{:.2f}".format(js) if js is not None
                    else ("SKIP" if res.get("skipped") else "  -"))
            row += " " + cell.ljust(13) + " |"
        print(row)

    # Ranking
    print("\n" + "-" * 72)
    print("RANKING (nach Avg-Judge-Score, grok-4.3 = Baseline)")
    print("-" * 72)
    print("  Rang | Modell              | Avg  | Delta | Tokens(ges) | Kosten    | $/Punkt")
    for rang, lbl in enumerate(ranking, 1):
        avg = avg_per_model[lbl]
        delta = avg - avg_per_model[baseline_lbl]
        tg = tokens_per_model[lbl]["gesamt"]
        cost = cost_per_model[lbl]
        cpp = cost_per_point[lbl]
        marker = " <- Baseline" if lbl == baseline_lbl else ""
        print("  {:>3d}  | {:18s} | {:.2f} | {:+.2f} | {:>10d} | ${:>7.4f} | ${:.4f}{}".format(
            rang, lbl, avg, delta, tg, cost, cpp, marker))

    # Skipped-Modelle
    skipped_summary = ausw.get("skipped", {})
    any_skipped = any(v for v in skipped_summary.values())
    if any_skipped:
        print("\n" + "-" * 72)
        print("UEBERSPRUNGENE TASKS (402/429/Fehler -> Modell-Block skip, Rest weiter)")
        print("-" * 72)
        for lbl, errs in skipped_summary.items():
            for e in errs:
                print("  " + lbl + " | " + e)

    # Preis-Tabelle
    print("\nPreise/1M Tokens (Input/Output):")
    for mid, lbl in MODELS:
        p = COST_PER_1M.get(mid, {"input": 0, "output": 0})
        print("  " + lbl.ljust(18) + ": $" + "{:.2f}".format(p["input"])
              + " / $" + "{:.2f}".format(p["output"]))

    # CAVEATS (Q.2)
    print("\n" + "-" * 72)
    print("CAVEATS (Q.2)")
    print("-" * 72)
    print("(1) RNG jetzt FIX (RUN_SEED=" + str(RUN_SEED)
          + ") -> Delta ist kausal (alter Rauschen-Caveat erledigt).")
    print("(2) Judge = grok-4.3 -> moeglicher Selbstbias zugunsten grok-4.3.")
    print("    Blind-Check (output/blind_check.md) = menschlicher Tiebreaker.")
    print("(3) GEGENPROBE seriell vs parallel: "
          + ("IDENTISCH (Parallelitaet aendert Vergleichbarkeit nicht)."
             if gp_ok else "MISMATCH -> siehe Report."))
    if bad:
        print("(4) WARN: Seed-Determinismus verletzt in: " + ", ".join(bad)
              + " -> Delta nicht voll kausal.")

    # Empfehlung
    print("\n" + "-" * 72)
    print("EMPFEHLUNG")
    print("-" * 72)
    best_lbl = ranking[0]
    best_delta = avg_per_model[best_lbl] - avg_per_model[baseline_lbl]
    if best_lbl == baseline_lbl:
        print("Kein Challenger schlaegt grok-4.3 (Punkt 1).")
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
        print("-> grok-4.3 bleibt Preis-Leistungs-Optimum.")

    print("\nGesamt-Laufzeit: " + str(round(overall_elapsed, 1)) + "s")
    print("Footer: " + FOOTER_TEXT)

    # Blind-Check
    rows_per_seed = {}
    for i, seed in enumerate(SEEDS):
        rows_per_seed[seed] = []
        for _, lbl in MODELS:
            res = results[lbl].get(seed, {})
            rows_per_seed[seed].append({
                "label": lbl,
                "spruch": res.get("spruch", ""),
                "judge_score": res.get("judge_score"),
                "fmt": FMTS[i],
            })
    write_blind_check(rows_per_seed, model_labels)

    # Finaler Report (partial=False, mit Gegenprobe + Verifikation)
    _save_report(results, model_labels, overall_elapsed, partial=False,
                 gegenprobe_ok=gp_ok, seed_verifikation=seed_verif)
    print("\nReport gespeichert: " + str(REPORT_PATH))


if __name__ == "__main__":
    main()
