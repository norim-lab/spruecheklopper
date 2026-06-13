"""
generator.py – Bauernspruch-Generator auf GLM-Basis (v2 mit System-Prompt).

Ablauf pro Spruch:
  1. Reimwort via lokaler API suchen (v12 mit Semantik)
  2. System-Prompt + User-Prompt mit Reimdaten an GLM senden
  3. JSON-Antwort parsen (Spruch + Self-Score + Metadaten)
  4. Reim-Check + Score-Validierung
  5. Bei Score < 3: retry mit anderem Reimwort (max 3x)

Features:
  - Vollstaendiger 12-Sektionen System-Prompt (SPRUECHEKLOPPER-Regelwerk)
  - API-basierte Reimwortsuche mit Semantik-Score-Priorisierung
  - Varianz-Tracking (Personas, Settings, Klang-Gruppen)
  - Self-Scoring durch LLM (0-5) + serverseitiger Reim-Check
  - Automatischer Modell-Fallback bei Rate-Limit (429)
  - Token-genauen Kosten-Tracking (Session + persistent)
"""

import json
import random
import re
import sys
import time
import unicodedata
from pathlib import Path

import requests

# ── Konfiguration ──────────────────────────────────────────────────────────────

DATA_PATH     = Path(__file__).parent.parent / "output"
COST_LOG_PATH = DATA_PATH / "cost_log.json"

GLM_API_URL   = "https://api.z.ai/api/paas/v4/chat/completions"
GLM_MODEL     = "glm-4.6"
GLM_FALLBACKS = ["glm-4-plus", "glm-5-turbo", "glm-4.7-flashx", "glm-4-32b-0414-128k",
                 "glm-4.5-air", "glm-4.5-flash", "glm-4.7-flash"]

# Grok (xAI) Konfiguration
GROK_API_URL   = "https://api.x.ai/v1/chat/completions"
GROK_MODEL     = "grok-4.3"
GROK_FALLBACKS = ["grok-3", "grok-3-mini"]
# Judge-Modell fuer Generate-then-rank: immer das staerkste verfuegbare Modell.
# Fallback-Kette in _llm_call greift automatisch, wenn grok nicht konfiguriert.
JUDGE_MODEL   = "grok-4.3"
GLM_TIMEOUT   = 30
MAX_RETRIES   = 3
TEMPERATURE   = 1.0
COST_LOG_MAX  = 1000

# Reim-Qualitaet (v14 DB-autoritativ, 10.06.2026)
SELF_SCORE_MIN      = 4       # Alles darunter = retry
MIN_WORDS_PER_GROUP = 4       # Mindest-Reimpartner pro Klanggruppe

# Silben-/Rhythmus-Schwellen (v16 Metrik-Check)
MIN_SILBEN       = 5   # Mindest-Silben pro Zeile
MAX_SILBEN       = 14  # Maximal-Silben pro Zeile
MAX_SILBEN_SPANNE = 6  # max. Differenz zwischen kuerzester und laengster Zeile
MAX_FREMDWORT_RATIO = 0.4     # Max 40% Fremdwoerter pro Gruppe
MAX_HAEUFIGKEIT     = 18      # DeReWo-Rang > 18 = zu exotisch fuer Bauernsprache
# MIN_EDIT_DISTANCE entfernt — Levenshtein blockierte gute Reime (Bier/Tier)
SCHEMA_GROUPS = {
    "AABB-4": 2, "ABAB-4": 2, "AABBCC-6": 3, "AABBA-5": 2, "AA-2": 1,
}
REJECT_SUFFIXE = {"lich", "ung", "keit", "heit", "ig"}

# v15: abstrakte Schlusswörter = Verlegenheitslösung -> Hard-Reject
ABSTRAKT_BLACKLIST = {
    "bedacht", "würde", "bedauern", "stolz", "ehre", "sinn",
    "wonne", "freude", "anmut", "gemüt", "seele", "verstand",
    "wut", "chaos", "geschrei", "pracht", "knallrot", "elan",
    "trubel", "wirrwarr", "tumult", "zorn", "rage",
}

# Lokale API fuer Reim-Lookup
RHYME_API_URL = "http://127.0.0.1:5000/api/sprachnudel/search"

_PREISE = {
    "glm-5-turbo":         {"input": 0.0012,  "output": 0.004},
    "glm-5v-turbo":        {"input": 0.0012,  "output": 0.004},
    "glm-5.1":             {"input": 0.0014,  "output": 0.0044},
    "glm-5":               {"input": 0.001,   "output": 0.0032},
    "glm-4.7":             {"input": 0.0006,  "output": 0.0022},
    "glm-4.7-flashx":      {"input": 0.00007, "output": 0.0004},
    "glm-4.7-flash":       {"input": 0.0,     "output": 0.0},
    "glm-4.6":             {"input": 0.0006,  "output": 0.0022},
    "glm-4.6v":            {"input": 0.0003,  "output": 0.0009},
    "glm-4.6v-flashx":     {"input": 0.00004, "output": 0.0004},
    "glm-4.6v-flash":      {"input": 0.0,     "output": 0.0},
    "glm-4.5":             {"input": 0.0006,  "output": 0.0022},
    "glm-4.5v":            {"input": 0.0006,  "output": 0.0018},
    "glm-4.5-air":         {"input": 0.0002,  "output": 0.0011},
    "glm-4.5-airx":        {"input": 0.0011,  "output": 0.0045},
    "glm-4.5-flash":       {"input": 0.0,     "output": 0.0},
    "glm-4-plus":          {"input": 0.001,   "output": 0.001},
    "glm-4-32b-0414-128k": {"input": 0.0001,  "output": 0.0001},
    "glm-ocr":             {"input": 0.00003, "output": 0.00003},
    "autoglm-phone-multilingual": {"input": 0.0005, "output": 0.0005},
    "grok-4.3":  {"input": 0.003, "output": 0.015},
    "grok-3":    {"input": 0.005, "output": 0.025},
    "grok-3-mini": {"input": 0.0003, "output": 0.001},
}

_DEBUG = False

# ── Live-Status + Cancel ─────────────────────────────────────────────────────

_GEN_STATUS = {
    "running": False,
    "log": [],          # [{"ts": ..., "msg": ...}, ...]
    "cancel": False,
    "started": None,
    "model": None,
}
_GEN_LOG_MAX = 200

def _status_log(msg):
    """Fuegt eine Statusmeldung hinzu (fuer Live-Log im Frontend)."""
    from datetime import datetime
    _GEN_STATUS["log"].append({
        "ts": datetime.now().strftime("%H:%M:%S"),
        "msg": str(msg),
    })
    if len(_GEN_STATUS["log"]) > _GEN_LOG_MAX:
        _GEN_STATUS["log"] = _GEN_STATUS["log"][-_GEN_LOG_MAX:]
    if _DEBUG:
        print("  [generator] " + str(msg), file=sys.stderr)

def _status_reset():
    _GEN_STATUS["running"] = False
    _GEN_STATUS["log"] = []
    _GEN_STATUS["cancel"] = False
    _GEN_STATUS["started"] = None
    _GEN_STATUS["model"] = None

def _status_cancel():
    _GEN_STATUS["cancel"] = True

def get_gen_status():
    """Liefert den aktuellen Generierungs-Status fuer das Frontend."""
    import copy
    s = copy.deepcopy(_GEN_STATUS)
    s["log_count"] = len(s["log"])
    return s

# ── Varianz-Tracking ──────────────────────────────────────────────────────────

_VARIANCE_PATH = DATA_PATH / "variance_state.json"

def _load_variance():
    if _VARIANCE_PATH.exists():
        try:
            with open(_VARIANCE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_10_personas": [], "last_10_settings": [],
            "last_20_klang_gruppen": [], "last_20_reimwoerter": []}

def _save_variance(state):
    _VARIANCE_PATH.parent.mkdir(exist_ok=True)
    with open(_VARIANCE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def _update_variance(cast, setting, klang_gruppen, reimwoerter):
    s = _load_variance()
    for persona in (cast or []):
        if persona not in s["last_10_personas"]:
            s["last_10_personas"].insert(0, persona)
    s["last_10_personas"] = s["last_10_personas"][:10]
    if setting and setting not in s["last_10_settings"]:
        s["last_10_settings"].insert(0, setting)
    s["last_10_settings"] = s["last_10_settings"][:10]
    for kg in (klang_gruppen or []):
        if kg not in s["last_20_klang_gruppen"]:
            s["last_20_klang_gruppen"].insert(0, kg)
    s["last_20_klang_gruppen"] = s["last_20_klang_gruppen"][:20]
    for rw in (reimwoerter or []):
        rw_lower = rw.lower()
        if rw_lower not in [x.lower() for x in s["last_20_reimwoerter"]]:
            s["last_20_reimwoerter"].insert(0, rw_lower)
    s["last_20_reimwoerter"] = s["last_20_reimwoerter"][:20]
    _save_variance(s)


# ── Session-Kostentracker ──────────────────────────────────────────────────────

_SESSION = {"prompt_tokens": 0, "completion_tokens": 0, "kosten_usd": 0.0, "calls": 0}


def session_stats():
    s = _SESSION
    return {
        "calls":  s["calls"],
        "tokens": {"prompt": s["prompt_tokens"], "completion": s["completion_tokens"],
                    "gesamt": s["prompt_tokens"] + s["completion_tokens"]},
        "kosten_usd": round(s["kosten_usd"], 6),
    }


def session_reset():
    _SESSION.update({"prompt_tokens": 0, "completion_tokens": 0, "kosten_usd": 0.0, "calls": 0})


def _load_cost_log():
    if COST_LOG_PATH.exists():
        try:
            return json.load(open(COST_LOG_PATH, encoding="utf-8"))
        except Exception:
            pass
    return {"entries": []}


def _save_cost_entry(model, pt, ct, kosten):
    from datetime import datetime, timezone
    log = _load_cost_log()
    log["entries"].append({
        "ts":         datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model":      model,
        "pt":         pt,
        "ct":         ct,
        "kosten_usd": round(kosten, 8),
    })
    if len(log["entries"]) > COST_LOG_MAX:
        log["entries"] = log["entries"][-COST_LOG_MAX:]
    COST_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(COST_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def cost_report():
    from datetime import datetime, timezone, timedelta
    log = _load_cost_log()
    now = datetime.now(timezone.utc)

    heute_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    woche_start = heute_start - timedelta(days=heute_start.weekday())
    monat_start = heute_start.replace(day=1)

    def _empty():
        return {"calls": 0, "tokens": 0, "kosten_usd": 0.0}

    buckets = {"gesamt": _empty(), "monat": _empty(), "woche": _empty(), "heute": _empty()}

    for e in log.get("entries", []):
        try:
            ts = datetime.strptime(e["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except Exception:
            continue
        tokens = e.get("pt", 0) + e.get("ct", 0)
        kosten = e.get("kosten_usd", 0.0)
        for bucket, seit in [("gesamt", None), ("monat", monat_start),
                              ("woche", woche_start), ("heute", heute_start)]:
            if seit is None or ts >= seit:
                buckets[bucket]["calls"]      += 1
                buckets[bucket]["tokens"]     += tokens
                buckets[bucket]["kosten_usd"] += kosten

    for b in buckets.values():
        b["kosten_usd"] = round(b["kosten_usd"], 6)

    return buckets


def _session_add(model, pt, ct):
    kosten = _calc_cost(model, pt, ct)
    _SESSION["prompt_tokens"]     += pt
    _SESSION["completion_tokens"] += ct
    _SESSION["kosten_usd"]        += kosten
    _SESSION["calls"]             += 1
    if pt > 0 or ct > 0:
        _save_cost_entry(model, pt, ct, kosten)


# ── Logging (leitet an _status_log weiter) ──────────────────────────────────────

def _log(msg):
    _status_log(msg)


# ── Kosten ─────────────────────────────────────────────────────────────────────

def _calc_cost(model, prompt_tokens, completion_tokens):
    p = _PREISE.get(model.lower(), {"input": 0.001, "output": 0.001})
    return (prompt_tokens * p["input"] + completion_tokens * p["output"]) / 1000


# ── GLM-Client (Chat-Format mit system + user messages) ────────────────────────

def test_api_key(api_key=None, model=None):
    m = model or GLM_MODEL
    if m.startswith("grok"):
        key = api_key or _read_grok_api_key()
        headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
        body = {"model": m, "temperature": 0.1, "max_tokens": 10,
                "messages": [{"role": "user", "content": "OK"}]}
        try:
            r = requests.post(GROK_API_URL, headers=headers, json=body, timeout=10)
            if r.status_code == 401:
                return {"ok": False, "error": "Grok API-Key ungueltig (401)"}
            if r.status_code == 429:
                return {"ok": True, "warning": "Rate-Limit – Key funktioniert"}
            r.raise_for_status()
            return {"ok": True, "model": r.json().get("model", m)}
        except requests.RequestException as e:
            return {"ok": False, "error": str(e)}
    else:
        key = api_key or _read_api_key()
        headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
        body = {"model": m, "temperature": 0.1, "max_tokens": 10,
                "messages": [{"role": "user", "content": "OK"}]}
        try:
            r = requests.post(GLM_API_URL, headers=headers, json=body, timeout=10)
            if r.status_code == 401:
                return {"ok": False, "error": "API-Key ungueltig (401)"}
            if r.status_code == 429:
                return {"ok": True, "warning": "Rate-Limit – Key funktioniert"}
            r.raise_for_status()
            return {"ok": True, "model": r.json().get("model", m)}
        except requests.RequestException as e:
            return {"ok": False, "error": str(e)}


def _glm_call(api_key, messages, model=None):
    """Sendet messages-Array (system + user) an GLM API mit Fallback-Kette."""
    primaer = model or GLM_MODEL
    kandidaten = [primaer] + [f for f in GLM_FALLBACKS if f != primaer]
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}

    for m in kandidaten:
        # Thinking-Modelle brauchen deutlich mehr Tokens (reasoning_content + content)
        is_thinking = "flash" in m.lower() or "4.7" in m or "4.5" in m
        max_tok = 4096 if is_thinking else (2000 if "turbo" in m.lower() or "5" in m else 1000)
        body = {
            "model": m,
            "temperature": TEMPERATURE,
            "max_tokens": max_tok,
            "messages": messages,
        }
        timeout = 60 if "turbo" in m.lower() or "5" in m else GLM_TIMEOUT
        try:
            r = requests.post(GLM_API_URL, headers=headers, json=body, timeout=timeout)
            if r.status_code == 429 and m != kandidaten[-1]:
                _log("Rate-Limit (" + m + "), 3s Pause dann Fallback -> " + kandidaten[kandidaten.index(m)+1])
                time.sleep(3)
                continue
            r.raise_for_status()
            data = r.json()
            msg = data["choices"][0]["message"]
            text = (msg.get("content") or "").strip()
            usage = data.get("usage", {})
            pt = usage.get("prompt_tokens", 0)
            ct = usage.get("completion_tokens", 0)
            if m != primaer:
                _log("Antwort via Fallback-Modell " + m)
            return text, pt, ct, m
        except requests.RequestException as e:
            _log("GLM-Fehler (" + m + "): " + str(e))
            if m == kandidaten[-1]:
                return None, 0, 0, m
            time.sleep(2)
            continue
        except (KeyError, IndexError) as e:
            _log("GLM-Parse-Fehler (" + m + "): " + str(e))
            if m == kandidaten[-1]:
                return None, 0, 0, m
            continue
    return None, 0, 0, primaer


def _grok_call(api_key, messages, model=None):
    """Sendet messages-Array an xAI (Grok) API mit Fallback-Kette."""
    primaer = model or GROK_MODEL
    kandidaten = [primaer] + [f for f in GROK_FALLBACKS if f != primaer]
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}

    for m in kandidaten:
        body = {
            "model": m,
            "temperature": TEMPERATURE,
            "max_tokens": 2000,
            "messages": messages,
        }
        try:
            r = requests.post(GROK_API_URL, headers=headers, json=body, timeout=60)
            if r.status_code == 429 and m != kandidaten[-1]:
                _log("Grok Rate-Limit (" + m + "), 3s Pause")
                time.sleep(3)
                continue
            r.raise_for_status()
            data = r.json()
            msg = data["choices"][0]["message"]
            text = (msg.get("content") or "").strip()
            usage = data.get("usage", {})
            pt = usage.get("prompt_tokens", 0)
            ct = usage.get("completion_tokens", 0)
            if m != primaer:
                _log("Grok Antwort via Fallback-Modell " + m)
            return text, pt, ct, m
        except requests.RequestException as e:
            _log("Grok-Fehler (" + m + "): " + str(e))
            if m == kandidaten[-1]:
                return None, 0, 0, m
            time.sleep(2)
            continue
        except (KeyError, IndexError) as e:
            _log("Grok-Parse-Fehler (" + m + "): " + str(e))
            if m == kandidaten[-1]:
                return None, 0, 0, m
            continue
    return None, 0, 0, primaer


def _llm_call(messages, model=None):
    """Dispatcher: waehlt GLM oder Grok basierend auf Modellnamen."""
    m = model or GLM_MODEL
    if m.startswith("grok"):
        api_key = _read_grok_api_key()
        if not api_key:
            _log("Kein Grok API-Key gefunden, falle zurueck auf GLM")
            return _glm_call(_read_api_key(), messages, model=GLM_MODEL)
        return _grok_call(api_key, messages, model=m)
    else:
        api_key = _read_api_key()
        if not api_key:
            return None, 0, 0, GLM_MODEL
        return _glm_call(api_key, messages, model=m)


# ── LLM-as-Judge (Generate-then-rank) ─────────────────────────────────────────

def _judge_sprueche(kandidaten, model=JUDGE_MODEL):
    """Bewertet mehrere fertige Sprueche unabhaengig und waehlt den besten.

    Input:  Liste valider Spruch-Dicts (jedes mit Schluessel "spruch").
    Output: {"best_index": int, "scores": [float], "begruendung": str}
            None bei leerer Eingabe.
    """
    if not kandidaten:
        return None
    if len(kandidaten) == 1:
        return {"best_index": 0,
                "scores": [kandidaten[0].get("self_score", 0)],
                "begruendung": "einziger Kandidat"}

    liste = "\n\n".join(
        "[" + str(i) + "]\n" + k.get("spruch", "")
        for i, k in enumerate(kandidaten)
    )
    judge_prompt = (
        "Du bist strenger Jury-Kopf fuer SPRUECHEKLOPPER-Bauernsprueche.\n"
        "Bewerte jeden Spruch 0-5 nach: sauberer Reim, Rhythmus, "
        "Kausalitaet (Z1->Z4), ein Subjekt durchgehend, sinnliches Schlusswort, "
        "echte Pointe, trockener Witz.\n\n"
        "PUNKTABZUG PFLICHT bei:\n"
        "(a) Fuellreim — Reimwort ohne inhaltlichen Bezug zum Spruch\n"
        "(b) Pointe nur benannt/erzaehlt statt durch ein konkretes Bild gezeigt\n"
        "(c) Abstraktem Schlusswort (z.B. Wut, Chaos, Stolz, Sinn)\n\n"
        "Nenne in der Begrundung fuer jeden Spruch das je schwaechste Element.\n"
        "Waehle den BESTEN. Antworte NUR als JSON:\n"
        '{"best_index": <int>, "scores": [<float pro Spruch>], '
        '"begruendung": "<kurz, je Spruch das schwaechste Element>"}\n\n'
        + liste
    )
    messages = [
        {"role": "system",
         "content": "Du bewertest Humor ehrlich und hart. "
                    "Kein Spruch ist automatisch eine 5. "
                    "Fuellreime und abstrakte Schlusswoerter sind sofortiger "
                    "Punktabzug. Eine Pointe muss als Bild gezeigt werden, "
                    "nicht erzaehlt."},
        {"role": "user", "content": judge_prompt},
    ]
    _log("Judge-Bewertung von " + str(len(kandidaten)) +
         " Kandidaten (" + str(model) + ")")
    antwort, pt, ct, used = _llm_call(messages, model=model)
    _session_add(used, pt, ct)
    parsed = _parse_json_response(antwort) or {}
    idx = parsed.get("best_index", 0)
    if not isinstance(idx, int) or not (0 <= idx < len(kandidaten)):
        idx = 0
    return {"best_index": idx,
            "scores": parsed.get("scores", []),
            "begruendung": parsed.get("begruendung", "")}


# ── Reim-Lookup via lokaler API ────────────────────────────────────────────────

def _lookup_rhymes(wort):
    """Sucht Reimpartner ueber lokale v12 API. Liefert rohes API-Resultat."""
    try:
        r = requests.get(RHYME_API_URL, params={"q": wort}, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        _log("API-Lookup fehlgeschlagen fuer '" + wort + "': " + str(e))
    return None


def _extract_klang(api_data):
    """Extrahiert den Reimklang aus v12 API-Daten.
    Die v12 API hat kein Top-Level 'klang'-Feld.
    Alternative: IPA des Suchworts oder Suffix der ersten Reimwoerter.
    """
    # Versuch 1: direktes klang-Feld (falls vorhanden)
    klang = api_data.get("klang", "")
    if klang:
        return klang

    # Versuch 2: IPA-Suffix (letzte betonte Silbe)
    ipa_list = api_data.get("ipa", [])
    if ipa_list:
        ipa = ipa_list[0].strip("[]")
        # Letzte 2-4 Zeichen als Klang-Gruppe
        if len(ipa) >= 2:
            return ipa[-min(4, len(ipa)):]

    # Versuch 3: Gemeinsames Suffix der ersten 3 Reimwoerter
    rhymes = api_data.get("rhymes", [])
    if len(rhymes) >= 2:
        w1 = rhymes[0].get("wort", "").lower()
        w2 = rhymes[1].get("wort", "").lower()
        # Gemeinsames Ende finden
        for i in range(1, min(len(w1), len(w2), 6)):
            if w1[-i:] == w2[-i:]:
                common = w1[-i:]
            else:
                break
        else:
            common = ""
        if common and len(common) >= 2:
            return common

    # Fallback: Suchwort-Suffix
    suchwort = api_data.get("suchwort", "")
    return suchwort[-3:].lower() if len(suchwort) >= 3 else suchwort.lower()



# ── Seed-Woerter Pool ─────────────────────────────────────────────────────────

_SEED_WOERTER = [
    "Haus", "Stall", "Bauer", "Kuh", "Bier", "Schnaps", "Mist", "Heu",
    "Feld", "Wald", "Wiese", "Garten", "Zaun", "Traktor", "Mistgabel",
    "Melken", "Ernte", "Schwein", "Hahn", "Hund", "Katze", "Pferd",
    "Esel", "Gans", "Ziege", "Schaf", "Opa", "Oma", "Pfarrer", "Wirt",
    "Sonne", "Regen", "Wind", "Nacht", "Feuer", "Kalt", "Warm",
    "Brot", "Wurst", "Käse", "Milch", "Ei", "Topf", "Ofen",
    "Liebe", "Herz", "Geld", "Arbeit", "Ruh", "Schlaf", "Tod",
    "Krieg", "Frieden", "Stolz", "Wut", "Angst", "Mut", "Kraft",
    "Witz", "Lachen", "Weinen", "Traum", "Zeit", "Jahr", "Leben",
]


# ── Hilfsfunktionen fuer Reim-Qualitaet ────────────────────────────────────────

def _is_likely_fremdwort(wort):
    """Heuristik: Ist das Wort wahrscheinlich ein Fremdwort?"""
    if len(wort) > 10:
        return True
    fremdwort_endings = (
        "tion", "sion", "ment", "ance", "ence", "ität",
        "ismus", "ieren", "abel", "ibel", "thek", "pell",
    )
    wl = wort.lower()
    for end in fremdwort_endings:
        if wl.endswith(end):
            return True
    return False


def _seed_pool_fuer_thema(thema):
    """Holt den Seed-Pool fuer ein Thema ueber /api/sprachnudel/topic-search.

    Echtes Response-Format:
      {"woerter": [{"wort": "...", ...}, ...], ...}
    Gibt Liste von Wort-Strings zurueck. Leer bei Fehler/ohne Ergebnis.
    """
    if not thema:
        return []
    try:
        r = requests.get("http://127.0.0.1:5000/api/sprachnudel/topic-search",
                         params={"q": thema}, timeout=10)
        if r.status_code == 200:
            woerter = [w.get("wort") or w.get("suchwort")
                       for w in r.json().get("woerter", [])]
            return [w for w in woerter if w]
    except Exception as e:
        _log("topic-search fehlgeschlagen fuer '" + str(thema) + "': " + str(e))
    return []


def _pick_seed_v2(rnd, fmt="AABB-4", thema=None):
    """Zieht N Klanggruppen via API, jede mit >= MIN_WORDS_PER_GROUP Woertern.
    v14: klang direkt aus DB, partner-Set fuer autoritative Validierung,
    semantik_score-gewichtete Auswahl, silben-Bevorzugung,
    haeufigkeit-basierte Fremdwortfilterung.
    thema: optional – wenn gesetzt, werden Seeds aus dem Themenfeld gezogen
           (Fallback auf _SEED_WOERTER bei leerem Ergebnis).
    Gibt Liste von dicts zurueck:
        [{"klang": ..., "seed": ..., "woerter": [...], "partner": set(...)}, ...]
    """
    n_groups = SCHEMA_GROUPS.get(fmt, 2)
    variance = _load_variance()
    penalized_klang = set(variance.get("last_20_klang_gruppen", []))

    # Seed-Pool: thema-gesteuert oder normaler Random-Pool
    if thema:
        seed_pool = _seed_pool_fuer_thema(thema)
        if seed_pool:
            _log("Seed-Pool fuer Thema '" + str(thema) + "': " +
                 str(len(seed_pool)) + " Woerter")
        else:
            _log("Themen-Pool leer – Fallback auf _SEED_WOERTER")
            seed_pool = _SEED_WOERTER
    else:
        seed_pool = _SEED_WOERTER

    chosen = []
    seen_klang = set()
    attempts = 0
    max_attempts = n_groups * 15

    while len(chosen) < n_groups and attempts < max_attempts:
        attempts += 1
        wort = rnd.choice(seed_pool)
        data = _lookup_rhymes(wort)
        if not data or not data.get("rhymes"):
            continue

        # Fix 2: klang direkt aus der DB, Fallback nur wenn noetig
        klang = data.get("klang") or _extract_klang(data)
        if klang in seen_klang:
            continue

        suchwort = data.get("suchwort", wort)
        suchwort_silben = data.get("silben", data.get("suchwort_silben", 2))

        # Reimwoerter sammeln mit allen DB-Metadaten
        candidates = []
        for r in data.get("rhymes", []):
            w = r.get("wort", "")
            if not w:
                continue
            silben = r.get("silben", 2)
            if silben > 4 or len(w) > 12:
                continue

            # Fix 3: haeufigkeit-basierte Filterung (wenn verfuegbar)
            haeuf = r.get("haeufigkeit", None)
            if haeuf is not None and haeuf > MAX_HAEUFIGKEIT:
                continue

            # Fallback auf Heuristik wenn kein haeufigkeit-Feld
            if haeuf is None and _is_likely_fremdwort(w):
                continue

            sem_score = r.get("semantik_score", 0.0)
            candidates.append({
                "wort": w,
                "silben": silben,
                "semantik_score": sem_score,
                "haeufigkeit": haeuf,
            })

        # Duplikate entfernen
        seen_w = set()
        unique = []
        for c in candidates:
            if c["wort"].lower() not in seen_w:
                unique.append(c)
                seen_w.add(c["wort"].lower())
        candidates = unique

        if len(candidates) < MIN_WORDS_PER_GROUP:
            continue

        # Fremdwort-Check (Fallback-Ratio)
        fw_count = sum(1 for c in candidates if _is_likely_fremdwort(c["wort"]))
        fw_ratio = fw_count / len(candidates)
        if fw_ratio > MAX_FREMDWORT_RATIO:
            _log("Klanggruppe '" + klang + "' uebersprungen: " +
                 str(round(fw_ratio * 100)) + "% Fremdwoerter")
            continue

        # Penalisierte Klanggruppe skippen (nur als erste Gruppe)
        if klang in penalized_klang and len(chosen) == 0:
            continue

        # ── Fix 4+5: semantik_score-Gewichtung + silben-Bevorzugung ──
        # Gewicht: Basis 1, + semantik_score * 5, + silben-match-Bonus
        weighted = []
        for c in candidates:
            gewicht = 1 + int(c["semantik_score"] * 5)
            # Bevorzuge Reimpartner mit gleicher Silbenzahl wie Seed
            if c["silben"] == suchwort_silben:
                gewicht += 3
            # Frische Reimwoerter leicht bevorzugen
            if c["wort"].lower() not in [x.lower() for x in variance.get("last_20_reimwoerter", [])]:
                gewicht += 1
            weighted.append((c, gewicht))

        # Gewichtetes Sampling (statt purem rnd.sample)
        pool = []
        for c, g in weighted:
            pool.extend([c] * max(g, 1))
        rnd.shuffle(pool)

        sample = []
        sampled_set = set()
        for c in pool:
            if c["wort"].lower() not in sampled_set:
                sample.append(c["wort"])
                sampled_set.add(c["wort"].lower())
            if len(sample) >= 5:
                break
        # Falls Pool zu klein, auffuellen
        for c in candidates:
            if c["wort"].lower() not in sampled_set and len(sample) < 5:
                sample.append(c["wort"])
                sampled_set.add(c["wort"].lower())

        # ── Fix 1: partner-Set (voller DB-Reimsatz fuer Validierung) ──
        partner = {c["wort"].lower() for c in candidates}
        partner.add(suchwort.lower())

        chosen.append({
            "klang":   klang,
            "seed":    suchwort,
            "woerter": sample,
            "partner": partner,
            "themed_rhymes": data.get("themed_rhymes", []),
        })
        seen_klang.add(klang)
        _log("Klanggruppe " + str(len(chosen)) + ": '" + klang + "' (seed: " +
             suchwort + ") mit " + str(len(candidates)) + " DB-Partnern, gewaehlt: " +
             ", ".join(sample))

    if len(chosen) < n_groups:
        _log("WARNUNG: Nur " + str(len(chosen)) + "/" + str(n_groups) +
             " brauchbare Klanggruppen gefunden nach " + str(attempts) + " Versuchen")

    return chosen


# ── System-Prompt (12-Sektionen SPRUECHEKLOPPER Regelwerk) ────────────────────

SYSTEM_PROMPT = """# SYSTEM-PROMPT: SPRUECHEKLOPPER Bauernspruch-Generator

## 0. REIM-DISZIPLIN (wichtigste Regel — niemals abweichen!)
ARBEITSREIHENFOLGE pro Zeilenpaar:
1. Wähle Endwort A (z.B. "Stall")
2. Hole via API alle Wörter mit gleichem `klang` → echte Reimpartner
3. Wähle Endwort B aus dieser Liste (z.B. "Fall", "All", "Knall")
4. ERST JETZT die Zeile drumherum bauen
→ Verboten: Zeile schreiben und hoffen, dass sich am Ende etwas reimt.
→ Self-Check: Reimt sich Zeilenende 1 phonetisch exakt auf 2? Und 3 auf 4?
  Wenn nein → Score 0, komplett verwerfen.

## 0b. VERBOTEN (Hard-Reject in der Pipeline)

1. IDENTISCHE REIME: Dasselbe Wort darf NIE zweimal am Zeilenende stehen.
   - VERBOTEN: "...ganz blind / ...dabei blind"
   - VERBOTEN: "...Hyazinth / ...bunten Hyazinth"

2. RÜHRENDE REIME: Reimpartner dürfen sich nicht nur durch ein
   Beugungs- oder Pluralsuffix unterscheiden.
   - VERBOTEN: "blind / blinde", "Knecht / Knechte", "geht / gehst"

3. FAULE ENDUNGS-REIME: Folgende Endungen NICHT als Reim verwenden,
   wenn beide Zeilen sie teilen: -lich, -ung, -keit, -heit, -ig
   (Sie reimen sich nur grammatisch, nicht klanglich interessant.)

4. FREMDWORT-CLUSTER: Maximal 1 Fremdwort pro Spruch. Bauernsprache!
   - VERBOTEN: "homozygot / homozygot", "Hyazinth / Labyrinth"

## 0c. PFLICHT

- Wenn du innerhalb der gegebenen Klanggruppen KEINEN sauberen Reim
  findest, gib AUSSCHLIESSLICH zurück:
  {"error": "no_clean_rhyme", "grund": "<kurze begründung>"}
  → NIEMALS doppeln oder Endungen biegen, um die Form zu retten.

- self_score MUSS ehrlich sein. Sprüche mit identischem Reim ODER
  rührendem Reim MÜSSEN self_score ≤ 2 bekommen.

## 0d. MASTERFORMEL (Dramaturgie — Standard-Archetyp)
Eine Figur, aufrecht in Zeile 1 → eine normale Handlung → kausale Kette
mit EINEM Subjekt → die Figur erleidet ihre eigene unausweichliche
Konsequenz → konkretes sinnliches Bild als letztes Wort.

5 KERNREGELN:
1. FORM: AABB oder ABAB. Rhythmus trägt — kein Füllsel, um den Reim zu retten.
2. SPRACHE: ländlich, derb, konkret. KEINE Abstrakta wie "mit Bedacht",
   "in Würde", "mit Bedauern", "voller Stolz" — Verlegenheitslösungen.
3. LOGIK: Kausalität ist Pflicht. Zeile 1 VERURSACHT Zeile 4.
   Nicht: Figur tut A, jemand anderes beobachtet B.
4. AUFBAU: eine Figur, EIN Subjekt durchgehend. Kein neues Personal in
   Zeile 3 (der "Opa-Fehler").
5. POINTE: das LETZTE Wort ist ein konkretes sinnliches Bild —
   riechbar/hörbar/spürbar. Nicht verstehen, fühlen.
→ Ausnahmen (Tier-POV, "Bäuerin überlistet Bauer", Dorftratsch) dürfen vom
  "Figur straft sich selbst"-Schema abweichen, aber NIE von Kausalität + Ein-Subjekt.

## 1. ROLLE
Du bist ein Generator für moderne Bauernsprüche im Stil des YouTube-Shorts-Kanals
SPRUECHEKLOPPER. Du erzeugst kurze, gereimte Vierzeiler mit Hof-Setting,
trockenem Humor und einer Pointe am Schluss.

## 2. TON & CHARAKTER
- Bäuerlich-derb + kalauerhaft-albern + bitter-ehrlich
- Augenzwinkernd, bodenständig, mit klarer Pointe
- Versteckte sexuelle/derbe Anspielungen erlaubt — plump-explizit wird verworfen
- Stilanker: Otto-Waalkes-Reim trifft TikTok-Trockenheit

## 3. STRIKTE NO-GOS (→ Score 0, verwerfen)
- Diskriminierung (rassistisch, sexistisch, homophob, etc.)
- Heikle politische Themen (Parteien, aktuelle Konflikte)
- Religiöse Spitzen
- Plump-explizite Sexualität

## 4. STRUKTUR (Pflicht)
- 4 Zeilen AABB (Standard) ODER ABAB (Kreuzreim, anspruchsvoller) oder 2 Zeilen AA
- Aufbau: Bild → Pointe — die Pointe MUSS in Zeile 4 sitzen, nie früher
- Sauberer phonetischer Endreim — kein optischer Reim, kein Füll-Reim
- Gleichmäßiger Rhythmus — sprechbar, ~8 Silben pro Zeile (laut-vorlesen-Test)
- Kein Wort-auf-sich-selbst (Haus/Haus verboten)
- Gleicher Wortstamm nur sparsam (trinkt/ertrinkt okay)

## 5. CAST (Personen-PPool — bewusst rotieren!)
Klassisch: Bauer, Bäuerin, Knecht, Magd, Opa, Oma, Schwiegermutter, Tante,
  Onkel, Nachbar, Nachbarin
Dorf: Pfarrer, Bürgermeister, Wirt, Wirtin, Postbote, Lehrer, Tierarzt,
  Dorfdepp, Dorfweiser, Schmied, Müller, Jäger, Förster, Schäfer, Sennerin
Extern (Culture-Clash): Stadtmensch, Tourist, Influencer, Vertreter,
  Politiker im Wahlkampf, Erbtante, Bio-Hof-Praktikant
Tiere: Hofhund, Scheunenkatze, Kater, Hahn, Henne, Gans, Ente, Ziege, Schaf,
  Pferd, Esel, Eber, Bulle, Bienen, Hofmaus, Rabe

## 6. STORY-ARCHETYPEN (Setting-Pool — durchrotieren!)
Hof-Szenen: Heuernte, Schlachtfest, Schnapsbrennen, Melken, Mistgabel-Drama,
  Sturm überm Hof, Erntedank
Dorf: Dorffest, Kirmes, Schützenfest, Stammtisch, Frühschoppen, Sonntagsmesse,
  Beichtstuhl, Bauernmarkt, Viehmarkt, Dorfhochzeit, Beerdigung
Saison/Outdoor: Pilze sammeln, Jagd am Hochsitz, Frühlingsblüte, Eisheilige,
  Hundstage, Almabtrieb
Culture-Cllash (modern!): Stadtbesuch im Dorf, WLAN auf dem Land, Lieferando
  findet den Hof nicht, Tinder-Date des Knechts, E-Auto an der Dorfzapfsäule,
  Influencer dreht Reel im Stall, Bauer auf TikTok, Solaranlage trifft
  Schwiegermutter, ChatGPT-Beratung am Stammtisch
Twist-Mechaniken (wer treibt die Pointe?): Bäuerin überlistet Bauer, Tier ist
  klüger als Mensch, Pfarrer hat Geheimnis, Schwiegermutter eskaliert, Knecht &
  Magd-Romanze, Opa liefert Lebensweisheit, Tier-POV, Dorftratsch wendet sich,
  Stadtmensch versteht nichts, Generationenkonflikt

## 7. VARIANZ-REGELN (zwingend!)
- Über die letzten 10 Sprüche max. 3x dasselbe Personen-Duo
- Mind. jeder 5. Spruch OHNE klassisches Bauer/Bäuerin-Duo
- Mind. jeder 8. Spruch mit Culture-Clash / modernem Setting
- Setting-Wiederholung erst nach mind. 5 Sprüchen
- Twist-Mechanik alle 3-4 Sprüche wechseln

## 8. DATENBASIS
Die Reimwörter werden dir im User-Prompt mitgeliefert (aus sprachnudel_export.v12.json).
Nutze NUR diese vorgegebenen Reimwörter als Zeilenenden.

## 9. AUTO-SCORING (0-5)
5 = Kausalität (Z1→Z4) + Ein-Subjekt + sinnliches Schlusswort + sauberer Reim + Pointe
4 = wie 5, aber Pointe leicht absehbar ODER kleiner Rhythmus-Stolperer
3 = reimt sauber, solide, aber Pointe schwach/brav
2 = reimt, aber Füll-Reim oder keine Pointe
1 = holpriger Reim ODER inhaltlich kaum Sinn
0 = reimt nicht / unbrauchbar / No-Go verletzt
Abzug zwingend bei: neuem Subjekt in Z3, abstraktem Schlusswort,
  fehlender kausaler Kette zwischen Z1 und Z4.

## 10. OUTPUT-FORMAT (JSON pro Spruch)
{
  "spruch": "Zeile1\\nZeile2\\nZeile3\\nZeile4",
  "format": "AABB-4",
  "subjekt": "Bauer",
  "kausal": true,
  "letztes_wort": "Mist",
  "letztes_wort_sinnlich": true,
  "cast": ["Bauer", "Bäuerin"],
  "setting": "Stammtisch",
  "thema": "Bier",
  "reimwoerter": ["stinkt", "trinkt", "klar", "war"],
  "klang_gruppen": ["inkt", "ar"],
  "hook_vorschlag": "Wenn das Leben nach Schweiß riecht…",
  "self_score": 5,
  "score_begruendung": "Sauberer Reim, klare Pointe"
}

## 11. REFERENZ-BEISPIELE (5/5 — Ziel-Sound)

Klassisch (Goldstandard):
  Wer morgens schwitzt und abends stinkt,
  und trotzdem noch sein Bierchen trinkt,
  der hat den Sinn des Lebens klar —
  mehr braucht es nicht. So ist's und immer war.

Bäuerin-Twist:
  Der Bauer trinkt sein Bier im Steh'n,
  die Bäuerin lässt ihn gerne geh'n.
  Er geht nicht weit, er geht zur Kuh —
  die Kuh macht: Muh. Und schaut ihm zu.

Tier-POV:
  Die Kuh kaut Gras und denkt sich was,
  beim Melken wird ihr Euter nass.
  Sie schaut den Bauern an und sinnt:
  „Wer ist hier eigentlich das Rind?"

Pfarrer:
  Der Pfarrer schenkt den Wein gern ein,
  doch trinkt am liebsten selbst allein.
  Er segnet fromm das Abendmahl —
  und füllt sein Glas zum dritten Mal.

Influencer (modern):
  Der Influencer filmt im Stall,
  posiert im Heu für jeden Fall.
  Er rutscht im Mist, das Handy bricht —
  viral ist er. Nur schön ist's nicht.

Oma:
  Die Oma strickt und sagt kein Wort,
  sie hört nur zu an ihrem Ort.
  Der Bauer prahlt, die Oma lacht —
  sie weiß genau, wer's Werk hier macht.

Stadtbesuch:
  Der Onkel kommt vom Stadtbüro,
  im feinen Schuh, gelaunt und froh.
  Er tritt in Mist bis tief hinein —
  so fühlt sich echtes Landglück. Fein.

Kurzform (2-Zeiler Wetterreim):
  Schwitzt der Hahn verdächtig auf dem Grill,
  wird am Hof auf einmal alles still.

## 12. STIL-CHECKLISTE (vor jedem Output durchgehen)
[ ] Reimpaar 1↔2 und 3↔4 phonetisch exakt?
[ ] Pointe in Zeile 4 (nicht früher)?
[ ] Rhythmus sprechbar (laut vorlesen)?
[ ] Kein Füll-Reim?
[ ] Cast/Setting nicht wiederholt?
[ ] Kein No-Go?
[ ] Trocken-ironischer Twist statt braver Beschreibung?"""


# ── User-Prompt Builder ────────────────────────────────────────────────────────


def _build_dynamic_examples(n=3):
    """Zieht die Top-judge_score-Sprueche aus dem Archiv und formatiert sie
    als zusaetzliche Referenz-Beispiele fuer den System-Prompt.

    Gibt einen String zurueck (leer wenn Archiv leer oder Fehler).
    Die statischen 5/5-Beispiele im SYSTEM_PROMPT bleiben als Fallback.
    """
    try:
        from spruch_app import archive
        top = archive.get_top_by_judge(n=n, min_judge=4)
    except Exception as e:
        _log("Dynamische Beispiele fehlgeschlagen: " + str(e))
        return ""

    if not top:
        return ""

    block = "\n\n## 13. TOP-SPRUECHE AUS DEM ARCHIV (Judge-Score >= 4 — nachahmen!)\n"
    for i, eintrag in enumerate(top, 1):
        spruch = eintrag.get("spruch", "").strip()
        if not spruch:
            continue
        score = eintrag.get("judge_score", "?")
        block += "\n  [" + str(i) + "] (Judge " + str(score) + "/5):\n"
        # Spruch mit Einrueckung
        for zeile in spruch.splitlines():
            block += "  " + zeile.strip() + "\n"
    return block


def _build_user_prompt_v2(klang_gruppen, mode, fmt="AABB-4", drehscheibe=None):
    """Baut den User-Prompt mit MEHREREN Klanggruppen (v14).
    Gibt dem LLM eine AUSWAHL an Reimwoertern pro Gruppe + themed_rhymes.
    """
    variance = _load_variance()

    anzahl = ("4 Zeilen (" + fmt.split("-")[0] + ")"
              if mode == "long" else "2 Zeilen (AA)")

    # Klanggruppen-Beschreibung bauen
    gruppen_text = ""
    if fmt == "AABB-4" and len(klang_gruppen) >= 2:
        gruppen_text = (
            "Klang-Gruppe A (Zeile 1+2): \"" + klang_gruppen[0]["klang"] + "\"\n"
            "  Verfuegbare Reimwoerter: " + ", ".join(klang_gruppen[0]["woerter"]) + "\n"
            "\n"
            "Klang-Gruppe B (Zeile 3+4): \"" + klang_gruppen[1]["klang"] + "\"\n"
            "  Verfuegbare Reimwoerter: " + ", ".join(klang_gruppen[1]["woerter"]) + "\n"
        )
    elif fmt == "ABAB-4" and len(klang_gruppen) >= 2:
        gruppen_text = (
            "Klang-Gruppe A (Zeile 1+3): \"" + klang_gruppen[0]["klang"] + "\"\n"
            "  Verfuegbare Reimwoerter: " + ", ".join(klang_gruppen[0]["woerter"]) + "\n"
            "\n"
            "Klang-Gruppe B (Zeile 2+4): \"" + klang_gruppen[1]["klang"] + "\"\n"
            "  Verfuegbare Reimwoerter: " + ", ".join(klang_gruppen[1]["woerter"]) + "\n"
        )
    elif len(klang_gruppen) >= 1:
        # Fallback: nur 1 Gruppe (z.B. AA-2)
        gruppen_text = (
            "Klang-Gruppe (alle Zeilen): \"" + klang_gruppen[0]["klang"] + "\"\n"
            "  Verfuegbare Reimwoerter: " + ", ".join(klang_gruppen[0]["woerter"]) + "\n"
        )
    else:
        gruppen_text = "(keine Klanggruppen verfuegbar)\n"

    # themed_rhymes: thematisch gruppierte Reimwoerter fuer Kohaerenz
    themed_text = ""
    all_themed = []
    for g in klang_gruppen:
        for t in g.get("themed_rhymes", []):
            all_themed.append(t)
    if all_themed:
        themed_text = "\nThematische Reim-Gruppen (fuer inhaltliche Kohaerenz):\n"
        seen_themen = set()
        for t in all_themed[:6]:
            thema = t.get("thema", "")
            if thema in seen_themen or thema == "Weitere":
                continue
            seen_themen.add(thema)
            woerter = t.get("woerter", [])[:6]
            themed_text += "  - " + thema + ": " + ", ".join(woerter) + "\n"

    # ── Drehscheibe: harte Vorgaben (gewaehlte Felder fix, Zufall = wie bisher) ──
    drehscheibe_text = ""
    if drehscheibe:
        vorgaben = []
        figur = drehscheibe.get("figur")
        setting = drehscheibe.get("setting")
        twist = drehscheibe.get("twist")
        dthema = drehscheibe.get("thema")
        if figur and figur.lower() not in ("zufall", "random", ""):
            vorgaben.append("  - Hauptfigur/Zentrale Person: \"" + figur + "\" (PFLICHT!)")
        if setting and setting.lower() not in ("zufall", "random", ""):
            vorgaben.append("  - Setting/Szene: \"" + setting + "\" (PFLICHT!)")
        if twist and twist.lower() not in ("zufall", "random", ""):
            vorgaben.append("  - Twist-Mechanik: \"" + twist + "\" (PFLICHT!)")
        if dthema and dthema.lower() not in ("zufall", "random", ""):
            vorgaben.append("  - Thema/Stoff: \"" + dthema + "\" (PFLICHT!)")
        if vorgaben:
            drehscheibe_text = (
                "\nDREHSCHEIBE — Harte Vorgaben (muessen alle erfuellt sein):\n"
                + "\n".join(vorgaben) + "\n"
            )

    prompt = """Generiere einen SPRUECHEKLOPPER Bauernspruch.

Format: """ + anzahl + """

""" + gruppen_text + themed_text + drehscheibe_text + """
VARIAZ-Tracking (diese Kombinationen vermeiden):
- Zuletzte genutzte Personas: """ + (", ".join(variance.get("last_10_personas", [])[:5]) or "keine") + """
- Zuletzte genutzte Settings: """ + (", ".join(variance.get("last_10_settings", [])[:5]) or "keine") + """

WICHTIG:
- Wähle JEDES Reimwort aus der jeweiligen Klang-Gruppe (nicht frei erfinden!)
- NIE dasselbe Wort doppelt als Reim verwenden
- NUR GANZ EINEN Spruch als JSON zurueckgeben
- self_score ehrlich bewerten"""

    return prompt

def _normalize(zeile):
    return zeile.lower().strip().rstrip("!.,;: \t\u201c\u201d\u201e\u201f")


def _check_rhyme(text, reimwoerter, mode):
    """Prueft ob die Zeilenenden auf die Reimwoerter passen."""
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    if mode == "long":
        if len(lines) < 4:
            _log("Reim-Check: zu wenig Zeilen (" + str(len(lines)) + ")")
            return False
        # Zeile 1+2 muessen auf reimwoerter[0] enden, 3+4 auf reimwoerter[1]
        paare = [(0, reimwoerter[0]), (1, reimwoerter[0]),
                 (2, reimwoerter[1]), (3, reimwoerter[1])]
    else:
        if len(lines) < 2:
            _log("Reim-Check: zu wenig Zeilen")
            return False
        paare = [(0, reimwoerter[0]), (1, reimwoerter[0])]

    for i, wort in paare:
        norm_line = _normalize(lines[i])
        norm_wort = wort.lower()
        if not norm_line.endswith(norm_wort):
            _log("Zeile " + str(i+1) + " endet nicht auf '" + wort + "' (endet auf: '" + norm_line[-20:] + "')")
            return False
    return True


def _parse_json_response(text):
    """Versucht die JSON-Antwort aus dem LLM-Output zu parsen."""
    if not text:
        return None

    # Versuch 1: Direkter JSON-Parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Versuch 2: JSON aus Markdown-Codeblock extrahieren
    import re
    m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Versuch 3: Erste { ... } Klammer finden
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end+1])
        except json.JSONDecodeError:
            pass

    return None


# ── Hard-Reject Validator (v14 DB-autoritativ) ────────────────────────────────

def _silben_in_zeile(zeile):
    """Schaetzt die Silbenanzahl einer Zeile (deutsche Vokalgruppen-Heuristik).

    Aufeinanderfolgende Vokale (a, e, i, o, u, ae, oe, ue, y) zaehlen als
    EINE Silbe. Gleiche Logik wie die bestehende suchwort_silben-Schaetzung.
    """
    if not zeile:
        return 0
    text = unicodedata.normalize("NFC", str(zeile)).lower()
    # Umlaut-Varianten normalisieren fuer die Regex
    text = text.replace("ä", "a").replace("ö", "o").replace("ü", "u")
    vokale = re.findall(r"[aeiouy]+", text)
    return len(vokale)


def validate_spruch(spruch_json, klang_gruppen=None):
    """Prueft LLM-Ergebnis autoritativ gegen die v12-DB-Gruppenpartner.

    Ablauf:
      (a) identische Reime
      (b) self_score-Schwelle
      (c) AUTORITATIVE Reimpruefung: Zeilenenden muessen in der
          DB-Klanggruppe stehen (klang_gruppen[x]["partner"])
      (d) rührende Reime — STAMM-basiert (blind/blinde), nicht Levenshtein
      (e) faule Endungs-Reime
      (f) Reimwoerter muessen am Zeilenende stehen

    Gibt (ok: bool, grund: str) zurueck.
    """
    def _norm(w):
        return unicodedata.normalize("NFKC", str(w)).lower().strip(".,!?;:\"'")

    # ── Zeilenenden extrahieren ──
    text = spruch_json.get("spruch", "")
    lines = [l.strip() for l in text.replace("\\n", "\n").splitlines() if l.strip()]
    ende = [_norm(l.split()[-1]) for l in lines if l.split()]

    # (j) Silben-/Rhythmus-Check (v16 Metrik)
    if lines:
        silben = [_silben_in_zeile(l) for l in lines]
        for i, s in enumerate(silben):
            if s < MIN_SILBEN:
                return False, "rhythmus: zeile " + str(i + 1) + " hat " + str(s) + " silben (< " + str(MIN_SILBEN) + ")"
            if s > MAX_SILBEN:
                return False, "rhythmus: zeile " + str(i + 1) + " hat " + str(s) + " silben (> " + str(MAX_SILBEN) + ")"
        spanne = max(silben) - min(silben)
        if spanne > MAX_SILBEN_SPANNE:
            return False, "rhythmus: silbenspanne " + str(spanne) + " zu gross (> " + str(MAX_SILBEN_SPANNE) + "): " + str(silben)

    # (a) identische Reime
    if len(ende) != len(set(ende)):
        dupes = [w for w in ende if ende.count(w) > 1]
        return False, "identischer reim: " + str(list(set(dupes)))

    # (b) self_score — NICHT MEHR als Reject-Kriterium (nur Sortier-Kriterium in generate_spruch_best)

    # (c) AUTORITATIVE Reimpruefung gegen die v12-DB
    fmt = spruch_json.get("format", "AABB-4")
    if klang_gruppen and len(ende) >= 2:
        gA = klang_gruppen[0].get("partner", set()) | {klang_gruppen[0].get("seed", "").lower()}
        if fmt == "ABAB-4" and len(ende) >= 4:
            # Kreuzreim: 1+3 in Gruppe A, 2+4 in Gruppe B
            if not (ende[0] in gA and ende[2] in gA):
                return False, "zeile 1/3 nicht in klanggruppe A laut DB: " + str([ende[0], ende[2]])
            if len(klang_gruppen) > 1:
                gB = klang_gruppen[1].get("partner", set()) | {klang_gruppen[1].get("seed", "").lower()}
                if not (ende[1] in gB and ende[3] in gB):
                    return False, "zeile 2/4 nicht in klanggruppe B laut DB: " + str([ende[1], ende[3]])
                if ende[0] in gB or ende[1] in gA:
                    return False, "gruppe A und B reimen identisch (AAAA)"
        else:
            # Paarreim: 1+2 in Gruppe A, 3+4 in Gruppe B
            if not (ende[0] in gA and ende[1] in gA):
                return False, "zeile 1/2 nicht in klanggruppe A laut DB: " + str(ende[:2])
            if len(klang_gruppen) > 1 and len(ende) >= 4:
                gB = klang_gruppen[1].get("partner", set()) | {klang_gruppen[1].get("seed", "").lower()}
                if not (ende[2] in gB and ende[3] in gB):
                    return False, "zeile 3/4 nicht in klanggruppe B laut DB: " + str(ende[2:4])
                # AAAA-Schutz: Gruppe A und B duerfen nicht identisch reimen
                if ende[0] in gB or ende[2] in gA:
                    return False, "gruppe A und B reimen identisch (AAAA)"

    # (d) rührender Reim — STAMM-basiert (blind/blinde), nicht Levenshtein
    # Bei AABB: 1+2, 3+4; bei ABAB: 1+3, 2+4
    if fmt == "ABAB-4" and len(ende) >= 4:
        pairs = [(ende[0], ende[2]), (ende[1], ende[3])]
    else:
        pairs = list(zip(ende[::2], ende[1::2]))
    for a, b in pairs:
        kurz, lang = sorted((a, b), key=len)
        if lang.startswith(kurz) and lang[len(kurz):] in {
            "e", "n", "en", "er", "es", "et", "st", "te", "s",
        }:
            return False, "ruehrender reim: " + a + "/" + b

    # (e) faule Endungs-Reime
    for a, b in pairs:
        for sfx in REJECT_SUFFIXE:
            if a.endswith(sfx) and b.endswith(sfx):
                return False, "fauler endungsreim: " + a + "/" + b + " (beide -" + sfx + ")"

    # (f) Reimwoerter muessen am Zeilenende stehen
    reim = [_norm(w) for w in spruch_json.get("reimwoerter", [])]
    if reim and set(reim) - set(ende):
        return False, ("reimwoerter nicht am zeilenende: " +
                       str(reim) + " vs " + str(ende))

    # (g) Schlusswort darf kein Abstraktum sein (v15 Sprache-Regel)
    if ende and ende[-1] in ABSTRAKT_BLACKLIST:
        return False, "abstraktes schlusswort: " + ende[-1]

    # (h) Masterformel-Flags ehrlich erzwingen (v15)
    if not spruch_json.get("kausal", False):
        return False, "keine kausale kette (Z1 verursacht Z4 nicht)"
    if not spruch_json.get("letztes_wort_sinnlich", False):
        return False, "schlusswort nicht sinnlich-konkret"

    # (i) ABAB verlangt Kreuzreim (1<->3, 2<->4), nicht Paarreim
    if spruch_json.get("format") == "ABAB-4" and len(ende) >= 4:
        if ende[0] == ende[1] or ende[2] == ende[3]:
            return False, "ABAB verlangt Kreuzreim, kein Paarreim"

    return True, "ok"


# ── Kern-Funktion ──────────────────────────────────────────────────────────────

def generate_spruch(api_key=None, mode="long", rnd=None, debug=False, model=None, thema=None, drehscheibe=None):
    """Generiert einen Bauernspruch mit v2 API-Lookup + System-Prompt.
    thema:       optional – steuert die Seed-Auswahl auf ein semantisches Feld.
    drehscheibe: optionales Dict {figur, setting, twist, thema, form} –
                 gewaehlte Felder werden als harte Vorgaben eingebaut.
    """
    global _DEBUG
    _DEBUG = debug
    _status_reset()
    _GEN_STATUS["running"] = True
    from datetime import datetime
    _GEN_STATUS["started"] = datetime.now().strftime("%H:%M:%S")
    used_model = model or GLM_MODEL
    _GEN_STATUS["model"] = used_model

    if rnd is None:
        rnd = random.Random()

    # ── Drehscheibe: thema und form ueberschreiben ──
    if drehscheibe:
        if isinstance(drehscheibe, str):
            try:
                drehscheibe = json.loads(drehscheibe)
            except (json.JSONDecodeError, TypeError):
                drehscheibe = {}
        d_thema = drehscheibe.get("thema", "")
        if d_thema and str(d_thema).lower() not in ("zufall", "random", ""):
            thema = d_thema
        d_form = drehscheibe.get("form", "")
        if d_form == "AABB":
            mode = "long"
        elif d_form == "ABAB":
            mode = "long"
        elif d_form == "kurz":
            mode = "short"

    total_pt = total_ct = 0

    _log("Starte Generierung — Modell: " + str(used_model) + " | Modus: " + mode +
         (("' | Thema: " + str(thema)) if thema else "") +
         (("' | Drehscheibe: " + str(drehscheibe)) if drehscheibe else ""))

    # v14: Multi-Klanggruppen-Seed statt einzelnes Seed-Wort
    # v15: ABAB mit ~30% Wahrscheinlichkeit aktivieren
    # Schritt 6: Drehscheibe.form kann fmt fixieren
    d_form = drehscheibe.get("form", "") if drehscheibe else ""
    if mode == "long":
        if d_form == "AABB":
            fmt = "AABB-4"
        elif d_form == "ABAB":
            fmt = "ABAB-4"
        else:
            fmt = rnd.choices(["AABB-4", "ABAB-4"], weights=[70, 30])[0]
    else:
        fmt = "AA-2"
    klang_gruppen = _pick_seed_v2(rnd, fmt=fmt, thema=thema)

    if not klang_gruppen:
        _log("Keine brauchbaren Klanggruppen, Fallback auf Legacy")
        gruppen = load_gruppen()
        if gruppen:
            return _generate_spruch_legacy(pick_gruppe(gruppen, rnd), api_key, mode, rnd, debug, model)
        return {"ok": False, "error": "Keine Klanggruppen verfuegbar", "spruch": ""}

    # Fuer Varianz-Tracking und User-Prompt
    klang_labels = [g["klang"] for g in klang_gruppen]
    _log("Klanggruppen: " + " + ".join(klang_labels))

    # ── Dynamische Few-Shots aus dem Archiv (einmal pro Generierung) ──
    dyn_examples = _build_dynamic_examples(n=3)
    system_prompt_full = SYSTEM_PROMPT + dyn_examples

    letzter_spruch = None
    best_result = None

    for versuch in range(1, MAX_RETRIES + 1):
        if _GEN_STATUS["cancel"]:
            _log("⚠ Abgebrochen durch Benutzer")
            _GEN_STATUS["running"] = False
            return {"ok": False, "error": "Abgebrochen", "spruch": letzter_spruch or ""}

        _log("Versuch " + str(versuch) + "/" + str(MAX_RETRIES))

        user_prompt = _build_user_prompt_v2(klang_gruppen, mode, fmt, drehscheibe=drehscheibe)
        messages = [
            {"role": "system", "content": system_prompt_full},
            {"role": "user", "content": user_prompt},
        ]

        _log("Sende an LLM (" + str(used_model) + ")...")
        antwort, pt, ct, actual_model = _llm_call(messages, model=used_model)
        total_pt += pt
        total_ct += ct
        _session_add(actual_model, pt, ct)
        _log("LLM Antwort: " + str(pt) + " prompt + " + str(ct) + " completion tokens")

        if not antwort:
            _log("Keine Antwort vom LLM")
            time.sleep(5)
            continue

        _log("Raw Antwort:\n" + antwort[:500])

        # JSON parsen
        parsed = _parse_json_response(antwort)
        if not parsed:
            _log("Konnte JSON nicht parsen")
            letzter_spruch = antwort
            continue

        # LLM hat kein sauberen Reim gefunden
        if parsed.get("error") == "no_clean_rhyme":
            _log("LLM meldet: kein sauberer Reim — " + str(parsed.get("grund", "")))
            continue

        spruch_text = parsed.get("spruch", "")
        if not spruch_text:
            _log("Leerer Spruch im JSON")
            continue

        letzter_spruch = spruch_text

        # ── Hard-Reject Validator (v14, autoritativ gegen DB) ──
        valid, reason = validate_spruch(parsed, klang_gruppen=klang_gruppen)
        if not valid:
            _log("VALIDIERUNG FEHLGESCHLAGEN: " + reason)
            continue

        self_score = parsed.get("self_score", 0)
        cast = parsed.get("cast", [])
        setting = parsed.get("setting", "")
        reimwoerter_llm = parsed.get("reimwoerter", [])
        klang_llm = parsed.get("klang_gruppen", klang_labels)
        hook = parsed.get("hook_vorschlag", "")
        score_begr = parsed.get("score_begruendung", "")

        _log("Spruch AKZEPTIERT nach " + str(versuch) + " Versuch(en), Self-Score: " + str(self_score))

        # Varianz-Tracking aktualisieren
        _update_variance(cast, setting, klang_llm, reimwoerter_llm)

        kosten = _calc_cost(used_model, total_pt, total_ct)
        result = {
            "ok": True,
            "spruch": spruch_text,
            "format": parsed.get("format", fmt),
            "subjekt": parsed.get("subjekt", ""),
            "kausal": parsed.get("kausal", False),
            "letztes_wort": parsed.get("letztes_wort", ""),
            "letztes_wort_sinnlich": parsed.get("letztes_wort_sinnlich", False),
            "cast": cast,
            "setting": setting,
            "thema": parsed.get("thema", ""),
            "reimwoerter": reimwoerter_llm,
            "klang_gruppen": klang_llm,
            "hook_vorschlag": hook,
            "self_score": self_score,
            "score_begruendung": score_begr,
            "szene": setting,
            "versuche": versuch,
            "score": self_score,
            "tokens": {"prompt": total_pt, "completion": total_ct},
            "kosten_usd": round(kosten, 6),
            "model": actual_model,
        }
        _GEN_STATUS["running"] = False
        return result

    # Alle Versuche fehlgeschlagen
    _log("Alle Versuche fehlgeschlagen – bester Fallback")
    kosten = _calc_cost(used_model, total_pt, total_ct)
    _GEN_STATUS["running"] = False
    return {
        "ok": False,
        "spruch": letzter_spruch or "(kein Output)",
        "reimwoerter": [],
        "szene": "",
        "versuche": MAX_RETRIES,
        "score": 0,
        "tokens": {"prompt": total_pt, "completion": total_ct},
        "kosten_usd": round(kosten, 6),
        "model": used_model,
    }


# ── Legacy-Unterstützung (fallback auf reimgruppen.jsonl) ────────────────────

def _resolve_data_path():
    for name in ("reimgruppen_derb.jsonl", "reimgruppen_alle.jsonl", "reimgruppen_clean.jsonl"):
        p = DATA_PATH / name
        if p.exists():
            return p
    return DATA_PATH / "reimgruppen_clean.jsonl"


def _generate_spruch_legacy(gruppe, api_key, mode="short", rnd=None, debug=False, model=None):
    """Fallback: altes System mit reimgruppen.jsonl + einfachem Prompt."""
    global _DEBUG
    _DEBUG = debug

    if rnd is None:
        rnd = random.Random()

    used_model = model or GLM_MODEL

    # Alte Wortauswahl
    reimwoerter = _pick_reimwoerter_legacy(gruppe, rnd, n=2)
    klang = gruppe.get("klang", "")

    user_msg = (
        "Schreib einen witzigen deutschen Bauernspruch mit genau "
        + ("4 Zeilen (AABB)." if mode == "long" else "2 Zeilen (AA).") + "\n"
        + "Zeile 1+2 enden auf: \"" + reimwoerter[0] + "\"\n"
    )
    if mode == "long" and len(reimwoerter) > 1:
        user_msg += "Zeile 3+4 enden auf: \"" + reimwoerter[1] + "\"\n"
    user_msg += "Thema: Landleben, Bauernhof, derber Humor.\n"
    user_msg += "Nur die Zeilen ausgeben, keine Erklrung."

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    total_pt = total_ct = 0
    letzter_spruch = None

    for versuch in range(1, MAX_RETRIES + 1):
        antwort, pt, ct, actual_model = _llm_call(messages, model=used_model)
        total_pt += pt
        total_ct += ct
        _session_add(actual_model, pt, ct)

        if not antwort:
            time.sleep(1)
            continue

        spruch = "\n".join([l.strip().strip('"') for l in antwort.strip().splitlines() if l.strip()][:4 if mode == "long" else 2])
        letzter_spruch = spruch

        if _check_rhyme(spruch, reimwoerter, mode):
            kosten = _calc_cost(used_model, total_pt, total_ct)
            return {"ok": True, "spruch": spruch, "reimwoerter": reimwoerter,
                    "szene": "", "klang_gruppen": [klang], "cast": [], "setting": "",
                    "hook_vorschlag": "", "self_score": 3, "score": 3,
                    "versuche": versuch, "format": "AABB-4" if mode == "long" else "AA-2",
                    "tokens": {"prompt": total_pt, "completion": total_ct},
                    "kosten_usd": round(kosten, 6), "model": actual_model}
        time.sleep(0.5)

    kosten = _calc_cost(used_model, total_pt, total_ct)
    return {"ok": False, "spruch": letzter_spruch or "(kein Output)",
            "reimwoerter": reimwoerter, "szene": "", "versuche": MAX_RETRIES,
            "score": 0, "tokens": {"prompt": total_pt, "completion": total_ct},
            "kosten_usd": round(kosten, 6), "model": used_model}


def _pick_reimwoerter_legacy(gruppe, rnd, n=2):
    """Alte Wortauswahl aus reimgruppen.jsonl."""
    pool = []
    for w in gruppe.get("woerter", []):
        if not isinstance(w, dict):
            continue
        wort = str(w.get("wort", "")).strip()
        if wort and len(wort) <= 10:
            pool.append(wort)
    if not pool:
        pool = [gruppe.get("suchwort", "Bauer")]
    rnd.shuffle(pool)
    result, seen = [], set()
    for wort in pool:
        if wort.lower() not in seen:
            result.append(wort)
            seen.add(wort.lower())
        if len(result) == n:
            break
    while len(result) < n:
        result.append(pool[0] if pool else "Bauer")
    return result


# ── Datei laden ────────────────────────────────────────────────────────────────

def load_gruppen(path=None):
    src = Path(path) if path else _resolve_data_path()
    gruppen = []
    with open(src, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    gruppen.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return gruppen


def pick_gruppe(gruppen, rnd=None):
    if rnd is None:
        rnd = random.Random()
    pool = [g for g in gruppen if g.get("group_type") != "small"] or gruppen
    return rnd.choice(pool)


# ── History / High-Level API ──────────────────────────────────────────────────

HISTORY_PATH = DATA_PATH / "generator_history.json"
_HISTORY_MAX = 50


def _load_history():
    if HISTORY_PATH.exists():
        try:
            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"entries": []}


def _save_history(h):
    HISTORY_PATH.parent.mkdir(exist_ok=True)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(h, f, ensure_ascii=False, indent=2)


def _add_history(spruch, reimwoerter, score, mode, kosten, klang=None,
                 cast=None, setting=None, hook=None, self_score=None):
    h = _load_history()
    h["entries"].append({
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "spruch": spruch,
        "reimwoerter": list(reimwoerter or []),
        "klang": klang or "",
        "cast": cast or [],
        "setting": setting or "",
        "hook_vorschlag": hook or "",
        "score": score,
        "self_score": self_score if self_score is not None else score,
        "mode": mode,
        "kosten_usd": kosten,
    })
    if len(h["entries"]) > _HISTORY_MAX:
        h["entries"] = h["entries"][-int(_HISTORY_MAX):]
    _save_history(h)


def get_history(limit: int = 20) -> list:
    h = _load_history()
    return h.get("entries", [])[-int(limit):][::-1]


def get_stats() -> dict:
    """Liefert Statistiken im Format, das die Dashboard-UI erwartet."""
    h = _load_history()
    entries = h.get("entries", [])
    gruppen = []
    try:
        gruppen = load_gruppen()
    except Exception:
        pass
    total_words = 0
    klang_with_words = 0
    for g in gruppen:
        woerter = g.get("woerter") or g.get("reimwoerter") or []
        if woerter:
            klang_with_words += 1
            total_words += len(woerter)
    if not entries:
        return {
            "count": 0, "history_count": 0,
            "klang_count": len(gruppen),
            "klang_with_words": klang_with_words,
            "total_words": total_words,
            "avg_score": 0, "kosten_usd": 0.0, "calls": 0,
        }
    scores = [e.get("score", 0) for e in entries]
    kosten = sum(e.get("kosten_usd", 0) for e in entries)
    return {
        "count": len(entries),
        "history_count": len(entries),
        "klang_count": len(gruppen),
        "klang_with_words": klang_with_words,
        "total_words": total_words,
        "avg_score": round(sum(scores) / len(scores), 2),
        "kosten_usd": round(kosten, 6),
        "total_usd": round(kosten, 6),
        "calls": len(entries),
    }


def clear_history() -> None:
    if HISTORY_PATH.exists():
        try:
            HISTORY_PATH.unlink()
        except Exception:
            pass


# ── High-Level: generate_spruch_v2 ────────────────────────────────────────

def _read_api_key() -> str:
    """Liest den GLM API-Key aus config.json oder ENV."""
    import os
    key = os.environ.get("GLM_API_KEY", "")
    if key:
        return key
    cfg = Path(__file__).parent.parent / "config.json"
    if cfg.exists():
        try:
            return json.load(open(cfg, encoding="utf-8")).get("api_key", "")
        except Exception:
            return ""
    return ""


def _read_grok_api_key() -> str:
    """Liest den Grok API-Key aus config.json oder ENV."""
    import os
    key = os.environ.get("GROK_API_KEY", "")
    if key:
        return key
    cfg = Path(__file__).parent.parent / "config.json"
    if cfg.exists():
        try:
            return json.load(open(cfg, encoding="utf-8")).get("grok_api_key", "")
        except Exception:
            return ""
    return ""


def get_available_models():
    """Liefert alle verfuegbaren Modelle mit Provider-Info."""
    grok_key = _read_grok_api_key()
    models = []
    # GLM Modelle
    models.append({"id": "glm-4.6", "name": "GLM-4.6", "provider": "zhipu", "default": True})
    for m in GLM_FALLBACKS:
        if m != "glm-4.6":
            models.append({"id": m, "name": m.replace("-", " ").title(), "provider": "zhipu"})
    # Grok Modelle (nur wenn API-Key vorhanden)
    if grok_key:
        models.append({"id": "grok-4.3", "name": "Grok-4.3", "provider": "xai"})
        for m in GROK_FALLBACKS:
            models.append({"id": m, "name": m.replace("-", " ").title(), "provider": "xai"})
    return models


def generate_spruch_best(mode: str = "long", candidates: int = 8,
                         min_score: int = 4, model: str = None,
                         judge_model: str = None, thema: str = None,
                         drehscheibe=None) -> dict:
    """High-Level: erzeugt mehrere valide Kandidaten und laesst einen
    separaten Judge-LLM den besten waehlen (Generate-then-rank).

    mode:         "long" (4-Zeiler) | "short" (2-Zeiler)
    candidates:   Anzahl vollstaendig zu erzeugender Kandidaten (KEIN Early-Break)
    min_score:    Mindest-Self-Score, ab dem ein Kandidat in die Judge-Auswahl kommt
    model:        Generier-Modell (z.B. "glm-4.6") oder None fuer Default
    judge_model:  Judge-Modell (Default: JUDGE_MODEL = staerkstes verfuegbares Modell)
    thema:        optional – steuert die Seed-Auswahl auf ein semantisches Feld
    drehscheibe:  optionales Dict {figur, setting, twist, thema, form}
    """
    api_key = _read_api_key()
    if not api_key:
        return {"ok": False, "error": "Kein API-Key (config.json)"}

    used_judge = judge_model or JUDGE_MODEL
    rnd = random.Random()

    # Drehscheibe als JSON-String normalisieren (fuer Archiv)
    drehscheibe_json = ""
    if drehscheibe:
        if isinstance(drehscheibe, str):
            drehscheibe_json = drehscheibe
        else:
            drehscheibe_json = json.dumps(drehscheibe, ensure_ascii=False)

    _log("Generate-then-rank: " + str(candidates) + " Kandidaten, Judge=" + str(used_judge) +
         ((", Thema=" + str(thema)) if thema else "") +
         ((", Drehscheibe=" + drehscheibe_json) if drehscheibe_json else ""))

    valid_pool = []       # valide Sprueche mit self_score >= min_score (Judge-Pool)
    fallback_pool = []    # alle ok-Ergebnisse (falls kein valider dabei ist)
    last_resort_pool = [] # ok:False-Versuche mit nicht-leerem Text (Notfall-Judge-Pool)

    for c in range(int(candidates)):
        if _GEN_STATUS["cancel"]:
            _log("Abgebrochen durch Benutzer (bei Kandidat " + str(c + 1) + ")")
            break
        _log("Kandidat " + str(c + 1) + "/" + str(candidates))
        r = generate_spruch(mode=mode, rnd=rnd, model=model, thema=thema,
                            drehscheibe=drehscheibe)
        score = r.get("self_score", r.get("score", 0))
        if not r.get("ok"):
            score = 0
        if r.get("ok"):
            fallback_pool.append(r)
            if score >= int(min_score):
                valid_pool.append(r)
        else:
            # ok:False — aber wenn Text da ist, als Last Resort sammeln
            notsatz = (r.get("spruch") or r.get("letzter_spruch") or "").strip()
            if notsatz and notsatz != "(kein Output)":
                r["ok"] = True  # als Judge-Kandidat markieren
                r["self_score"] = 0
                r["last_resort"] = True
                last_resort_pool.append(r)
                _log("Kandidat " + str(c + 1) + " als last_resort gesammelt (Validierung fehlgeschlagen, aber Text vorhanden)")

    # Auswahl-Pool: valide bevorzugen, sonst alle ok-Ergebnisse, sonst last_resort
    pool = valid_pool if valid_pool else fallback_pool
    if not pool:
        pool = last_resort_pool
    if not pool:
        _log("Keine validen Kandidaten erzeugt – liefere besten Fallback")
        return {"ok": False, "error": "kein output"}

    _log(str(len(pool)) + " Kandidaten fuer Judge-Bewertung")
    urteil = _judge_sprueche(pool, model=used_judge)
    if urteil is None:
        best = pool[0]
    else:
        idx = urteil["best_index"]
        best = pool[idx]
        scores = urteil.get("scores", [])
        if isinstance(scores, list) and 0 <= idx < len(scores):
            try:
                best["judge_score"] = float(scores[idx])
            except (TypeError, ValueError):
                best["judge_score"] = best.get("self_score", 0)
        else:
            best["judge_score"] = best.get("self_score", 0)
        best["judge_begruendung"] = urteil.get("begruendung", "")
        best["judge_scores"] = scores
        _log("Judge waehlt Kandidat " + str(idx) + " — Score " +
             str(best["judge_score"]) + " — " + str(best["judge_begruendung"]))

    # History aktualisieren
    if thema:
        best["thema"] = thema
    klang = ""
    if best.get("klang_gruppen"):
        klang = best["klang_gruppen"][0] if isinstance(best["klang_gruppen"], list) else str(best["klang_gruppen"])
    _add_history(
        spruch=best.get("spruch", ""),
        reimwoerter=best.get("reimwoerter", []),
        score=best.get("judge_score", best.get("score", 0)),
        mode=mode,
        kosten=best.get("kosten_usd", 0.0),
        klang=klang,
        cast=best.get("cast", []),
        setting=best.get("setting", best.get("szene", "")),
        hook=best.get("hook_vorschlag", ""),
        self_score=best.get("self_score", best.get("score", 0)),
    )

    # Drehscheibe fuer UI/Archiv-Trace ins Ergebnis schreiben
    if drehscheibe_json:
        best["drehscheibe"] = drehscheibe_json

    # ── Dauerhaftes SQLite-Archiv (wird nie truncatet) ──
    try:
        from spruch_app import archive
        archiv_id = archive.archive_spruch(best, drehscheibe=drehscheibe_json or None)
        if archiv_id:
            _log("Spruch im Archiv gespeichert (id=" + str(archiv_id) + ")")
        else:
            _log("Spruch bereits im Archiv vorhanden (Dedup)")
        # Auto-Favorit + Auto-Veroeffentlichung nur bei judge_score >= 4
        js = best.get("judge_score", 0)
        try:
            js = float(js)
        except (TypeError, ValueError):
            js = 0.0
        if archiv_id and js >= 4.0:
            archive.set_favorit(archiv_id, 1)
            archive.set_veroeffentlicht(archiv_id, 1)
            _log("Auto-Favorit + veroeffentlicht (judge_score=" + str(js) + " >= 4.0)")
    except Exception as e:
        _log("Archiv-Speicherung fehlgeschlagen: " + str(e))

    return best


def generate_spruch_v2(mode: str = "long", candidates: int = 5,
                       min_score: int = 4, model: str = None,
                       thema: str = None, drehscheibe=None) -> dict:
    """Duenner Wrapper auf generate_spruch_best (Signatur bleibt erhalten).
    Delegiert an den Generate-then-rank-Ablauf mit Judge-Bewertung.
    """
    return generate_spruch_best(mode=mode, candidates=candidates,
                                min_score=min_score, model=model,
                                thema=thema, drehscheibe=drehscheibe)


def generate_batch(anzahl: int = 3, mode: str = "long", candidates: int = 5,
                   min_score: int = 4, model: str = None,
                   thema: str = None, drehscheibe: str = None) -> list:
    """Erzeugt mehrere fertige Sprueche auf einmal.

    anzahl:       Anzahl fertiger Sprueche (jeder einzeln gejudged + archiviert)
    candidates:   Qualitaetsversuche PRO Spruch (nicht Anzahl Ergebnisse!)
    drehscheibe:  optionales Tag fuer das Archiv

    Gibt eine Liste von Ergebnis-Dicts zurueck. Jeder Spruch wird beim
    Durchlauf durch generate_spruch_best automatisch im Archiv gespeichert.
    """
    anzahl = max(1, min(int(anzahl), 20))
    ergebnisse = []
    _log("Batch-Generierung: " + str(anzahl) + " Sprueche" +
         ((", Thema=" + str(thema)) if thema else "") +
         ((", Drehscheibe=" + str(drehscheibe)) if drehscheibe else ""))

    for i in range(anzahl):
        if _GEN_STATUS["cancel"]:
            _log("Batch abgebrochen bei Spruch " + str(i + 1) + "/" + str(anzahl))
            break
        _log("=== Batch-Spruch " + str(i + 1) + "/" + str(anzahl) + " ===")
        r = generate_spruch_best(mode=mode, candidates=candidates,
                                 min_score=min_score, model=model,
                                 thema=thema, drehscheibe=drehscheibe)
        if r.get("ok"):
            if drehscheibe:
                r["drehscheibe"] = drehscheibe
                # Archiv-Eintrag aktualisieren
                try:
                    from spruch_app import archive
                    archive.DB_PATH  # sicherstellen, dass Modul geladen
                except Exception:
                    pass
            ergebnisse.append(r)
        else:
            _log("Spruch " + str(i + 1) + " fehlgeschlagen: " +
                 str(r.get("error", "?")))

    _log("Batch fertig: " + str(len(ergebnisse)) + "/" + str(anzahl) + " Sprueche")
    return ergebnisse


# ── Toxische Klanggruppen markieren (Einmal-Skript) ────────────────────────────

def mark_toxic_groups(v12_path=None):
    """Geht die v12-DB durch und markiert Gruppen mit < MIN_WORDS_PER_GROUP
    Partner oder > MAX_FREMDWORT_RATIO Fremdwoertern mit 'toxic': true.
    Einmal ausfuehren, spart Laufzeit-Filterung.
    """
    if v12_path is None:
        v12_path = DATA_PATH / "sprachnudel_export.v12.json"
    else:
        v12_path = Path(v12_path)

    if not v12_path.exists():
        _log("v12 JSON nicht gefunden: " + str(v12_path))
        return

    _log("Lade v12 JSON fuer Toxic-Markierung: " + str(v12_path))
    with open(v12_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    words = data if isinstance(data, list) else data.get("words", [])
    if not words:
        _log("Keine Woerter in v12 JSON gefunden")
        return

    # Nach klang gruppieren
    from collections import defaultdict
    klang_map = defaultdict(list)
    for entry in words:
        klang = entry.get("klang", "")
        if not klang:
            continue
        wort = entry.get("suchwort", "")
        if wort:
            klang_map[klang].append(wort)

    toxic_count = 0
    total_count = len(klang_map)
    for klang, woerter in klang_map.items():
        fw_count = sum(1 for w in woerter if _is_likely_fremdwort(w))
        ratio = fw_count / max(len(woerter), 1)
        is_toxic = len(woerter) < MIN_WORDS_PER_GROUP or ratio > MAX_FREMDWORT_RATIO
        if is_toxic:
            toxic_count += 1
        # Markiere in den originalen Eintraegen
        for entry in words:
            if entry.get("klang") == klang:
                entry["toxic"] = is_toxic

    # Zurueckschreiben
    with open(v12_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    _log("Toxic-Markierung abgeschlossen: " + str(toxic_count) + "/" +
         str(total_count) + " Klanggruppen als toxisch markiert")


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os

    api_key = os.environ.get("GLM_API_KEY", "")
    if not api_key:
        cfg = Path(__file__).parent.parent / "config.json"
        if cfg.exists():
            try:
                api_key = json.load(open(cfg, encoding="utf-8")).get("api_key", "")
            except Exception:
                pass
    if not api_key:
        print("FEHLER: GLM_API_KEY nicht gesetzt.", file=sys.stderr)
        sys.exit(1)

    mode  = sys.argv[1] if len(sys.argv) > 1 else "long"
    debug = "--debug" in sys.argv

    print("Modus: " + mode)
    print()

    result = generate_spruch(api_key, mode=mode, debug=debug)
    print(result.get("spruch", "(kein Output)"))
    print()

    if result.get("ok"):
        print("Cast        : " + str(result.get("cast", [])))
        print("Setting     : " + result.get("setting", ""))
        print("Reimwoerter : " + str(result.get("reimwoerter", [])))
        print("Self-Score  : " + str(result.get("self_score", "?")) + "/5")
        if result.get("score_begruendung"):
            print("Begruendung : " + result["score_begruendung"])
        if result.get("hook_vorschlag"):
            print("Hook        : " + result["hook_vorschlag"])
    else:
        print("Fehler      : " + str(result.get("error", "Reim fehlgeschlagen")))
        print("Reimwoerter : " + str(result.get("reimwoerter", [])))

    t  = result.get("tokens", {})
    pt = t.get("prompt", 0)
    ct = t.get("completion", 0)
    k  = result.get("kosten_usd", 0)
    print()
    print("Versuche    : " + str(result.get("versuche", "?")))
    print("Tokens      : " + str(pt) + " prompt + " + str(ct) + " completion = " + str(pt+ct) + " gesamt")
    print("Kosten      : $" + format(k, ".6f") + " USD  (~" + format(k*100, ".4f") + " Cent)")
    print("Modell      : " + str(result.get("model", "?")))

    s = session_stats()
    print()
    print("-- Session (diese Ausfuehrung) --")
    print("Calls       : " + str(s["calls"]))
    print("Tokens      : " + str(s["tokens"]["gesamt"]) + " gesamt")
    print("Kosten      : $" + format(s["kosten_usd"], ".6f") + " USD")

    r = cost_report()
    print()
    print("-- Kumulierte Kosten (aus cost_log.json) --")
    for label, key in [("Heute", "heute"), ("Woche", "woche"),
                        ("Monat", "monat"), ("Gesamt", "gesamt")]:
        b = r[key]
        print(label.ljust(8) + ": $" + format(b["kosten_usd"], ".6f") +
              "  (" + str(b["calls"]) + " Calls, " + str(b["tokens"]) + " Tokens)")
