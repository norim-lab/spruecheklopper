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
# Default-Modell: Grok 4.3 (staerker + stabiler als GLM-Fallback-Kette).
GLM_MODEL     = "grok-4.3"
GLM_FALLBACKS = ["grok-3", "grok-3-mini", "glm-4-plus", "glm-5-turbo", "glm-4.7-flashx", "glm-4-32b-0414-128k",
                 "glm-4.5-air", "glm-4.5-flash", "glm-4.7-flash"]

# Grok (xAI) Konfiguration
GROK_API_URL   = "https://api.x.ai/v1/chat/completions"
GROK_MODEL     = "grok-4.3"
GROK_FALLBACKS = ["grok-3", "grok-3-mini"]

# DeepInfra Konfiguration (dritter Provider — OpenAI-kompatibel)
DEEPINFRA_API_URL = "https://api.deepinfra.com/v1/openai/chat/completions"
DEEPINFRA_FALLBACKS = [
    "Qwen/Qwen3-235B-A22B-Instruct-2507",
    "deepseek-ai/DeepSeek-V3.2",
    "Qwen/Qwen3-Coder-480B-A35B-Instruct-Turbo",
    "deepseek-ai/DeepSeek-R1-0528",
]

# DeepInfra Embeddings (BGE-M3) — semantische Similaritaet fuer den
# Semantik-Score. Nutzt denselben API-Key wie die Chat-Modelle
# (DEEPINFRA_API_KEY / config.json "deepinfra_api_key"). Ohne Key werden
# Embeddings automatisch deaktiviert (Fallback auf regelbasierten Score).
DEEPINFRA_EMBEDDING_URL = "https://api.deepinfra.com/v1/inference/BAAI/bge-m3"
USE_EMBEDDINGS_DEFAULT  = True
EMBEDDING_BONUS_THRESHOLD = 0.75   # cosine-Similarity, ab der Bonus greift
EMBEDDING_BONUS           = 0.25   # additiv auf semantik_score [0..1]
EMBEDDING_CACHE_PATH = DATA_PATH / "embedding_cache.json"

# Judge-Modell fuer Generate-then-rank: immer das staerkste verfuegbare Modell.
# Fallback-Kette in _llm_call greift automatisch, wenn grok nicht konfiguriert.
JUDGE_MODEL   = "grok-4.3"
GLM_TIMEOUT   = 90
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
    # DeepInfra (Preise pro 1K Tokens in USD — Stand 2025)
    "Qwen/Qwen3-235B-A22B-Instruct-2507":              {"input": 0.0002, "output": 0.0006},
    "deepseek-ai/DeepSeek-V3.2":                        {"input": 0.0003, "output": 0.0011},
    "Qwen/Qwen3-Coder-480B-A35B-Instruct-Turbo":       {"input": 0.0004, "output": 0.0012},
    "deepseek-ai/DeepSeek-R1-0528":                     {"input": 0.0008, "output": 0.0024},
}

_DEBUG = False

# ── Live-Status + Cancel ─────────────────────────────────────────────────────

_GEN_STATUS = {
    "running": False,
    "log": [],          # [{"ts": ..., "msg": ...}, ...]
    "cancel": False,
    "started": None,
    "model": None,
    # J.5 IPA-Reim-Check Telemetrie
    "ipa_checks": 0,          # Reimpaare per IPA geprueft
    "heuristik_checks": 0,    # Reimpaare per _reim_endung-Fallback geprueft
    "ipa_mismatches": [],     # Wortpaare als Klartext ["w1/w2", ...]
    "fallback_fails": [],     # Wortpaare als Klartext ["w1/w2", ...]
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
    _GEN_STATUS["ipa_checks"] = 0
    _GEN_STATUS["heuristik_checks"] = 0
    _GEN_STATUS["db_checks"] = 0
    _GEN_STATUS["ipa_mismatches"] = []
    _GEN_STATUS["fallback_fails"] = []

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


def _deepinfra_call(api_key, messages, model=None):
    """Sendet messages-Array an DeepInfra API (OpenAI-kompatibel) mit Fallback-Kette."""
    primaer = model or DEEPINFRA_FALLBACKS[0]
    kandidaten = [primaer] + [f for f in DEEPINFRA_FALLBACKS if f != primaer]
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}

    for m in kandidaten:
        # Reasoning-Modelle (R1) brauchen mehr Output-Tokens
        max_tok = 3000 if "r1" in m.lower() else 2000
        body = {
            "model": m,
            "temperature": TEMPERATURE,
            "max_tokens": max_tok,
            "messages": messages,
        }
        try:
            r = requests.post(DEEPINFRA_API_URL, headers=headers, json=body, timeout=60)
            if r.status_code == 429 and m != kandidaten[-1]:
                _log("DeepInfra Rate-Limit (" + m + "), 3s Pause")
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
                _log("DeepInfra Antwort via Fallback-Modell " + m)
            return text, pt, ct, m
        except requests.RequestException as e:
            _log("DeepInfra-Fehler (" + m + "): " + str(e))
            if m == kandidaten[-1]:
                return None, 0, 0, m
            time.sleep(2)
            continue
        except (KeyError, IndexError) as e:
            _log("DeepInfra-Parse-Fehler (" + m + "): " + str(e))
            if m == kandidaten[-1]:
                return None, 0, 0, m
            continue
    return None, 0, 0, primaer


# ── DeepInfra Embeddings (BGE-M3) fuer den Semantik-Score ──────────────────────

_EMBEDDING_CACHE = None       # lazy geladenes dict: {wort_lower: [vec...]}
_EMBEDDING_CACHE_DIRTY = False
_EMBEDDING_AVAILABLE = None   # Drei-Wertig-Cache: None=unbekannt, True/False


def _embeddings_available(use_embeddings=True):
    """True, wenn Embeddings aktiv nutzbar sind (Flag + API-Key vorhanden).
    Ergebnis wird gecacht, damit wir nicht bei jedem Wort config.json lesen."""
    global _EMBEDDING_AVAILABLE
    if not use_embeddings:
        return False
    if _EMBEDDING_AVAILABLE is None:
        _EMBEDDING_AVAILABLE = bool(_read_deepinfra_api_key())
        if _EMBEDDING_AVAILABLE:
            _log("Embeddings aktiviert (BGE-M3 via DeepInfra)")
    return _EMBEDDING_AVAILABLE


def _load_embedding_cache():
    """Laedt den Embedding-Cache einmalig aus EMBEDDING_CACHE_PATH."""
    global _EMBEDDING_CACHE
    if _EMBEDDING_CACHE is not None:
        return _EMBEDDING_CACHE
    try:
        if EMBEDDING_CACHE_PATH.exists():
            _EMBEDDING_CACHE = json.load(
                open(EMBEDDING_CACHE_PATH, encoding="utf-8"))
        else:
            _EMBEDDING_CACHE = {}
    except Exception as e:
        _log("Embedding-Cache-Lesefehler: " + str(e) + " – starte leer")
        _EMBEDDING_CACHE = {}
    return _EMBEDDING_CACHE


def _save_embedding_cache():
    """Schreibt den Cache nur, falls er sich geaendert hat."""
    global _EMBEDDING_CACHE_DIRTY
    if not _EMBEDDING_CACHE_DIRTY:
        return
    try:
        EMBEDDING_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        json.dump(_EMBEDDING_CACHE,
                  open(EMBEDDING_CACHE_PATH, "w", encoding="utf-8"))
        _EMBEDDING_CACHE_DIRTY = False
    except Exception as e:
        _log("Embedding-Cache-Schreibfehler: " + str(e))


def _embed_words(words, api_key):
    """Holt BGE-M3-Vektoren fuer eine Wortliste (batched, cache-first).

    Gibt dict {wort: vector} zurueck (nur die Woerter mit Vektor).
    Gibt None zurueck, wenn der API-Call scheitert — Aufrufer faellt dann
    auf den regelbasierten Score zurueck.
    """
    global _EMBEDDING_CACHE_DIRTY
    cache = _load_embedding_cache()
    missing = [w for w in words if w.lower() not in cache]
    if missing:
        headers = {"Authorization": "Bearer " + api_key,
                   "Content-Type": "application/json"}
        body = {"inputs": missing}
        try:
            r = requests.post(DEEPINFRA_EMBEDDING_URL, headers=headers,
                              json=body, timeout=30)
            if r.status_code == 429:
                _log("Embedding-API Rate-Limit – überspringe (Cache reicht)")
                # Bereits gecachte Woerter trotzdem liefern
            else:
                r.raise_for_status()
                data = r.json()
                # DeepInfra liefert 'data' (Standard) oder 'embeddings'
                vecs = data.get("data") or data.get("embeddings") or []
                if len(vecs) != len(missing):
                    _log("Embedding-Anzahl ungleich Input ("
                         + str(len(vecs)) + "/" + str(len(missing))
                         + ") – skippe Batch")
                else:
                    for w, v in zip(missing, vecs):
                        cache[w.lower()] = v
                    _EMBEDDING_CACHE_DIRTY = True
                    _save_embedding_cache()
        except Exception as e:
            _log("Embedding-API-Fehler: " + str(e))
    result = {}
    for w in words:
        v = cache.get(w.lower())
        if v:
            result[w] = v
    return result or None


def _cosine_similarity(a, b):
    """Cosine-Similarity zweier float-Vektoren (0.0 bei Null-Vektor)."""
    import math
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _semantic_similarity_via_embedding(w1, w2, api_key=None, use_embeddings=True):
    """Cosine-Similarity zweier Woerter via BGE-M3 Embeddings.

    Rueckgabe:
      - float in [0.0, 1.0] bei Erfolg
      - None, wenn Embeddings deaktiviert, kein Key vorhanden oder Call scheitert
        (Aufrufer faellt dann auf den regelbasierten Score zurueck).
    """
    if not _embeddings_available(use_embeddings):
        return None
    api_key = api_key or _read_deepinfra_api_key()
    if not api_key:
        return None
    embs = _embed_words([w1, w2], api_key)
    if not embs or w1 not in embs or w2 not in embs:
        return None
    try:
        sim = _cosine_similarity(embs[w1], embs[w2])
        # Auf [0,1] beschneiden (BGE-M3 liefert teils >1 durch Rundung)
        return max(0.0, min(1.0, sim))
    except Exception as e:
        _log("Embedding-Similarity-Fehler (" + w1 + "/" + w2 + "): " + str(e))
        return None


def _embedding_semantic_bonus(seed, wort, base_score, use_embeddings=True):
    """Erweitert den regelbasierten semantik_score um einen Embedding-Bonus.

    +EMBEDDING_BONUS (0.25), wenn cosine_similarity(seed, wort)
    > EMBEDDING_BONUS_THRESHOLD (0.75). Sonst unveraendert.

    Rueckgabe: (neuer_score, similarity_or_None)
      similarity ist None, wenn Embeddings nicht verfuegbar/skippt wurden —
      dann bleibt base_score unveraendert (sauberer Fallback).
    """
    if not _embeddings_available(use_embeddings):
        return base_score, None
    sim = _semantic_similarity_via_embedding(seed, wort,
                                             use_embeddings=use_embeddings)
    if sim is None:
        return base_score, None
    if sim > EMBEDDING_BONUS_THRESHOLD:
        return min(base_score + EMBEDDING_BONUS, 1.0), sim
    return base_score, sim


def _llm_call(messages, model=None):
    """Dispatcher: waehlt GLM, Grok oder DeepInfra basierend auf Modellnamen."""
    m = model or GLM_MODEL
    ml = m.lower()
    # DeepInfra-Modelle erkennen (qwen, deepseek, oder "deepinfra" im Namen)
    if "qwen" in ml or "deepseek" in ml or "deepinfra" in ml:
        api_key = _read_deepinfra_api_key()
        if not api_key:
            _log("Kein DeepInfra API-Key gefunden, falle zurueck auf GLM")
            return _glm_call(_read_api_key(), messages, model=GLM_MODEL)
        return _deepinfra_call(api_key, messages, model=m)
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

# Schritt C: Derbheit-spezifische Zusaetze fuer System- und Judge-Prompt.
# Additiv — die Grundstruktur beider Prompts bleibt unveraendert. Die Derbheit
# steuert nur den Ton und ist KEINE neue Reimlogik.
DERBHEIT_BLOecke = {
    "mild": (
        "\n\n## 13. DERBHEIT (Nutzervorgabe: mild)\n"
        "Schreibe UNBEDINGT familienfreundlich. KEINE sexuellen Anspielungen, "
        "kein Fäkalhumor, keine derben Ausdrücke. Trocken-witzig und pointiert, "
        "aber brav. Eine Pointe darf ironisch sein, aber nicht obszön."
    ),
    "mittel": (
        "\n\n## 13. DERBHEIT (Nutzervorgabe: mittel)\n"
        "Leichte zweideutige Anspielungen und trockener Bauernhumor sind "
        "erlaubt. KEINE explizite Sexualitaet, KEIN Fäkalhumor. "
        "Zweideutigkeit darf subtil sein, plump-explizit bleibt verboten."
    ),
    "derb": (
        "\n\n## 13. DERBHEIT (Nutzervorgabe: derb)\n"
        "Voll derber, zweideutiger Bauernhumor ist ausdrücklich erwuenscht: "
        "versteckte sexuelle Anspielungen, Fäkalhumor, derbe Ausdruecke. "
        "Plump-explizite Sexualitaet bleibt verworfen (siehe 0b), aber "
        "anzoessige Zweideutigkeit ist Teil des Genres."
    ),
}

JUDGE_TON_BLOecke = {
    "mild": (
        "TON (Nutzervorgabe: mild): Die Sprueche SOLLFEN familienfreundlich sein. "
        "Bei derben Ausdruecken, sexuellen Anspielungen oder Fäkalhumor gibt es "
        "PUNKTABZUG — das ist hier nicht erwuenscht. Bewertet werden weiterhin "
        "Reim, Rhythmus, Kausalitaet und Bild statt benannter Pointe.\n\n"
    ),
    "mittel": (
        "TON (Nutzervorgabe: mittel): Leichte Anspielungen und trockener Humor "
        "sind erlaubt und KEIN Punktabzug. Aber explizite Sexualitaet oder "
        "Fäkalhumor ist hier nicht erwuenscht und gibt Punktabzug. Bewertet "
        "werden Reim, Rhythmus, Kausalitaet, echtes Bild statt benannter Pointe.\n\n"
    ),
    "derb": (
        "TON: Derber, zweideutiger Bauernhumor ist AUSDRUECKLICH ERLAUBT und "
        "KEIN Punktabzug. Bewertet werden Reim, Rhythmus, Kausalitaet, echtes "
        "Bild statt benannter Pointe — NICHT der Anstoss. Ein Spruch darf "
        "gerne derb-sexuelle Anspielungen oder Fäkalhumor enthalten, solange "
        "Reim + Kausalitaet + Pointe stimmen.\n\n"
    ),
}


def _build_derbheit_block(derbheit):
    """Liefert den additiven Derbheit-Block fuer den SYSTEM_PROMPT."""
    return DERBHEIT_BLOecke.get((derbheit or "derb").lower(),
                                 DERBHEIT_BLOecke["derb"])


def _build_judge_ton_block(derbheit):
    """Liefert den additiven TON-Block fuer den Judge-Prompt."""
    return JUDGE_TON_BLOecke.get((derbheit or "derb").lower(),
                                  JUDGE_TON_BLOecke["derb"])


def _judge_sprueche(kandidaten, model=JUDGE_MODEL, derbheit="derb"):
    """Bewertet mehrere fertige Sprueche unabhaengig und waehlt den besten.

    Input:  Liste valider Spruch-Dicts (jedes mit Schluessel "spruch").
    Output: {"best_index": int, "scores": [float], "begruendung": str}
            None bei leerer Eingabe.

    Schritt C: derbheit steuert den TON-Block im Judge-Prompt (additiv, die
    Kriterien bleiben identisch).
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
    ton_block = _build_judge_ton_block(derbheit)
    judge_prompt = (
        "Du bist strenger Jury-Kopf fuer SPRUECHEKLOPPER-Bauernsprueche.\n"
        "Bewerte jeden Spruch 0-5 nach: sauberer Reim, Rhythmus, "
        "Kausalitaet (Z1->Z4), ein Subjekt durchgehend, sinnliches Schlusswort, "
        "echte Pointe, trockener Witz, Konkretheit/Wortspiel der Pointe "
        "(wird ein Bild oder eine Doppeldeutigkeit genutzt, oder bleibt es abstrakt benannt?).\n\n"
        "PUNKTABZUG PFLICHT bei:\n"
        "(a) Fuellreim — Reimwort ohne inhaltlichen Bezug zum Spruch\n"
        "(b) Pointe nur benannt/erzaehlt statt durch ein konkretes Bild gezeigt\n"
        "(c) Abstraktem Schlusswort (z.B. Wut, Chaos, Stolz, Sinn)\n"
        "(d) Schlusswort nicht sinnlich-konkret (abstrakt/Gefuehl statt Bild)\n\n"
        + ton_block +
        "Nenne in der Begrundung fuer jeden Spruch das je schwaechste Element.\n"
        "Waehle den BESTEN. Antworte NUR als JSON:\n"
        '{"best_index": <int>, "scores": [<float pro Spruch>], '
        '"begruendung": "<kurz, je Spruch das schwaechste Element>"}\n\n'
        + liste
    )
    messages = [
        {"role": "system",
         "content": "Du bewertest Humor ehrlich und hart, aber du bist NICHT "
                    "der Sittenwächter. Derber, zweideutiger Bauernhumor ist "
                    "Teil des Genres — kein Grund fuer Punktabzug. "
                    "Kein Spruch ist automatisch eine 5. "
                    "Fuellreime und abstrakte Schlusswoerter sind sofortiger "
                    "Punktabzug. Eine Pointe muss als Bild gezeigt werden, "
                    "nicht erzaehlt."},
        {"role": "user", "content": judge_prompt},
    ]
    _log("Judge-Bewertung von " + str(len(kandidaten)) +
         " Kandidaten (" + str(model) + ", derbheit=" + str(derbheit) + ")")
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


# ── Kuratierte Reimgruppen (v22: aus build_reimgruppen.py) ─────────────────────
_KURATIERTE_GRUPPEN_CACHE = None
_KURATIERTE_GRUPPEN_OK = False


def _load_kuratierte_gruppen():
    """Laedt output/reimgruppen_derb.jsonl EINMAL (modulweit gecacht).
    Gibt Liste von Gruppen-dicts zurueck: {klang, seed, woerter[], partner_set}.
    Bei Fehler/leer: leere Liste + _KURATIERTE_GRUPPEN_OK=False.
    """
    global _KURATIERTE_GRUPPEN_CACHE, _KURATIERTE_GRUPPEN_OK
    if _KURATIERTE_GRUPPEN_CACHE is not None:
        return _KURATIERTE_GRUPPEN_CACHE

    p = DATA_PATH / "reimgruppen_derb.jsonl"
    if not p.exists():
        _log("WARN: Kuratierte Reimgruppen fehlen: " + str(p)
             + " – nutze API-Fallback")
        _KURATIERTE_GRUPPEN_CACHE = []
        return _KURATIERTE_GRUPPEN_CACHE

    gruppen = []
    try:
        with open(p, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    g = json.loads(ln)
                except Exception:
                    continue
                gruppen.append(g)
        if not gruppen:
            _log("WARN: Kuratierte Reimgruppen leer: " + str(p)
                 + " – nutze API-Fallback")
        else:
            _KURATIERTE_GRUPPEN_OK = True
            _log("Kuratierte Reimgruppen geladen: " + str(len(gruppen))
                 + " Gruppen aus " + str(p.name))
    except Exception as e:
        _log("WARN: Kuratierte Reimgruppen-Lesefehler: " + str(e)
             + " – nutze API-Fallback")
        gruppen = []

    _KURATIERTE_GRUPPEN_CACHE = gruppen
    return _KURATIERTE_GRUPPEN_CACHE


def _gruppe_passt_zum_thema(g, thema_pool_lower):
    """Schritt C Qualitaets-Guard: prueft, ob eine kuratierte Gruppe zum
    gewaehlten Thema passt. Dadurch bleibt der thema-Pfad auf den kuratierten
    Gruppen (mit Semantik + Reim-Treue) statt die Topic-API zu nutzen.

    Match-Strategie (case-insensitive, eine Bedingung reicht):
      (1) seed der Gruppe ist im Themen-Pool
      (2) eines der woerter[] ist im Themen-Pool
      (3) eines der seed_synonyme ist im Themen-Pool
      (4) eines der wort.synonyme ist im Themen-Pool

    thema_pool_lower: set/iterable von lowercased Woertern aus
                      _seed_pool_fuer_thema().
    """
    if not thema_pool_lower:
        return True  # kein Filter -> alles passt
    pool = set(thema_pool_lower)

    # (1) Seed
    seed = (g.get("seed") or "").lower()
    if seed and seed in pool:
        return True

    # (2) Woerter
    for w in (g.get("woerter") or []):
        ww = (w.get("wort") or "").lower()
        if ww and ww in pool:
            return True

    # (3) seed_synonyme (auf Gruppenebene)
    for syn in (g.get("seed_synonyme") or []):
        s = syn.lower()
        if s and s in pool:
            return True

    # (4) wort.synonyme
    for w in (g.get("woerter") or []):
        for syn in (w.get("synonyme") or []):
            s = syn.lower()
            if s and s in pool:
                return True

    return False


def _pick_seed_v2_aus_kuratisiert(rnd, fmt, n_groups, variance, penalized_klang,
                                  thema_pool_lower=None,
                                  use_embeddings=USE_EMBEDDINGS_DEFAULT):
    """NEUER Weg: sampelt Klanggruppen aus den kuratierten Dateien.
    Keine API-Calls. Gibt Liste von Gruppen-dicts im gleichen Format wie
    _pick_seed_v2_via_api zurueck.

    Schritt C Qualitaets-Guard: thema_pool_lower (optional) filtert die
    kuratierten Gruppen VOR dem Sampling — so bleibt der thema-Pfad auf
    den kuratierten Gruppen (mit Semantik + Reim-Treue aus J.3 + M).
    """
    gruppen = _load_kuratierte_gruppen()
    if not gruppen:
        return []

    # ── Qualitaets-Guard: thema-Filter anwenden ──
    if thema_pool_lower:
        gruppen = [g for g in gruppen
                   if _gruppe_passt_zum_thema(g, thema_pool_lower)]
        _log("Themen-Guard: " + str(len(gruppen)) + " kuratierte Gruppe(n) "
             + "passen zum Themen-Pool (size=" + str(len(thema_pool_lower))
             + ")")

    last_reimwoerter = [x.lower() for x in variance.get("last_20_reimwoerter", [])]

    chosen = []
    seen_klang = set()

    # Pool ohne penalisierte Klanggruppen (nur fuer die erste Wahl relevant)
    pool = list(gruppen)
    rnd.shuffle(pool)

    for g in pool:
        if len(chosen) >= n_groups:
            break
        klang = g.get("klang") or ""
        if not klang or klang in seen_klang:
            continue
        # Faule Endungs-Reime (REJECT_SUFFIXE aus validate_spruch) skippen:
        # Gruppen mit klang in {"lich","ung","keit","heit","ig"} wuerden
        # systematisch durch validate_spruch faellen (Hard-Reject).
        if any(klang.endswith(sfx) for sfx in REJECT_SUFFIXE):
            continue
        # Penalisierte Klanggruppe als ERSTE Wahl skippen
        if klang in penalized_klang and len(chosen) == 0:
            continue

        seed_wort = g.get("seed") or ""
        woerter_raw = g.get("woerter") or []
        partner_set = set((g.get("partner_set") or []))

        # Seed-Silben schaetzen (fuer Silben-Match-Bonus): aus erstem Wort
        seed_silben = None
        for w in woerter_raw:
            if w.get("wort", "").lower() == seed_wort.lower():
                seed_silben = w.get("silben")
                break
        if seed_silben is None:
            # Fallback: nimm das erste gefundene Silben-Feld
            seed_silben = woerter_raw[0].get("silben", 2) if woerter_raw else 2

        # ── Gewichtetes Sampling wie im alten Weg ──
        # Gewicht: 1 + int(semantik_score*5) + silben-match + frisch-bonus
        weighted = []
        for w in woerter_raw:
            ww = w.get("wort", "")
            if not ww:
                continue
            # Faule Endungen auf Wortebene skippen (REJECT_SUFFIXE aus
            # validate_spruch): Woerter wie "Regierung", "moeglich" etc.
            # wuerden systematisch durch faule_endung-Hard-Reject fallen.
            if any(ww.lower().endswith(sfx) for sfx in REJECT_SUFFIXE):
                continue
            sem_score = w.get("semantik_score") or 0.0
            # Embedding-Bonus (BGE-M3): +0.25 auf sem_score, wenn seed<->wort
            # semantisch sehr aehnlich (cosine > 0.75). Ohne Key/skip = keine
            # Aenderung (sauberer Fallback auf regelbasierten Score).
            sem_score, _sim = _embedding_semantic_bonus(
                seed_wort, ww, sem_score, use_embeddings=use_embeddings)
            silben = w.get("silben", 0)
            gewicht = 1 + int(sem_score * 5)
            if silben == seed_silben:
                gewicht += 3
            if ww.lower() not in last_reimwoerter:
                gewicht += 1
            weighted.append((w, max(gewicht, 1)))

        # Weighted sampling
        sample_pool = []
        meta_out = []
        for w, gg in weighted:
            sample_pool.extend([w] * gg)
        rnd.shuffle(sample_pool)

        sampled = []
        sampled_set = set()
        sampled_meta = []
        for w in sample_pool:
            ww = w.get("wort", "")
            key = ww.lower()
            if key in sampled_set:
                continue
            sampled.append(ww)
            sampled_set.add(key)
            sampled_meta.append({
                "wort": ww,
                "silben": w.get("silben", 0),
                "haeufigkeit": w.get("haeufigkeit"),
                "semantik_score": w.get("semantik_score") or 0.0,
                "semantik_gruende": w.get("semantik_gruende") or [],
                "ipa": w.get("ipa") or [],
                "synonyme": w.get("synonyme") or [],
                "definition": w.get("definition") or [],
            })
            if len(sampled) >= 8:
                break

        # Auffuellen falls Pool zu klein
        for w, _ in weighted:
            ww = w.get("wort", "")
            key = ww.lower()
            if key not in sampled_set and len(sampled) < 8:
                sampled.append(ww)
                sampled_set.add(key)
                sampled_meta.append({
                    "wort": ww,
                    "silben": w.get("silben", 0),
                    "haeufigkeit": w.get("haeufigkeit"),
                    "semantik_score": w.get("semantik_score") or 0.0,
                    "semantik_gruende": w.get("semantik_gruende") or [],
                    "ipa": w.get("ipa") or [],
                    "synonyme": w.get("synonyme") or [],
                    "definition": w.get("definition") or [],
                })

        if len(sampled) < MIN_WORDS_PER_GROUP:
            continue

        # Partner-Set aus der Gruppe (fuer autoritative Validierung)
        partner = set(x.lower() for x in partner_set)
        partner.add(seed_wort.lower())

        chosen.append({
            "klang": klang,
            "seed": seed_wort,
            "woerter": sampled,
            "partner": partner,
            "woerter_meta": sampled_meta,
            "themed_rhymes": [],
            "seed_synonyme": g.get("seed_synonyme") or [],
            "seed_definition": g.get("seed_definition") or [],
        })
        seen_klang.add(klang)
        _log("Klanggruppe " + str(len(chosen)) + " [kuratiert]: '" + klang
             + "' (seed: " + seed_wort + ") mit " + str(len(sampled))
             + " Woertern: " + ", ".join(sampled[:6]))

    if len(chosen) < n_groups:
        _log("Kuratiert: nur " + str(len(chosen)) + "/" + str(n_groups)
             + " Gruppen gefunden")
    return chosen


def _pick_seed_v2(rnd, fmt="AABB-4", thema=None,
                  use_embeddings=USE_EMBEDDINGS_DEFAULT):
    """v22: kuratierte Reimgruppen statt API-Lookups.
    Schritt C Qualitaets-Guard:
    - thema gesetzt  -> ZUERST kuratierte Gruppen filtern (mit Synonymen/
      Definition aus J.3 + M); nur FALLBACK auf Topic-API bei Mangel.
      So bleiben Semantik + Reim-Treue auch MIT Thema erhalten.
    - thema leer     -> kuratierte Gruppen, Fallback auf API bei Mangel.
    use_embeddings:  BGE-M3 Embedding-Bonus auf den Semantik-Score
                     (default True; ohne DeepInfra-Key sauberer No-Op).
    Return-Vertrag: Liste von dicts mit klang, seed, woerter[], partner (set).
    """
    n_groups = SCHEMA_GROUPS.get(fmt, 2)
    variance = _load_variance()
    penalized_klang = set(variance.get("last_20_klang_gruppen", []))

    # ── Schritt C Qualitaets-Guard ──
    # Pfad 1a: thema -> zuerst kuratierte Gruppen mit thema-Filter probieren
    if thema:
        thema_pool = _seed_pool_fuer_thema(thema)
        thema_pool_lower = set(w.lower() for w in thema_pool)
        if thema_pool_lower:
            chosen = _pick_seed_v2_aus_kuratisiert(
                rnd, fmt, n_groups, variance, penalized_klang,
                thema_pool_lower=thema_pool_lower,
                use_embeddings=use_embeddings)
            if len(chosen) >= n_groups:
                _log("Themen-Guard: " + str(len(chosen))
                     + " kuratierte Gruppe(n) fuer thema='" + str(thema)
                     + "' gefunden – Topic-API nicht noetig.")
                return chosen
            _log("Themen-Guard: nur " + str(len(chosen)) + "/"
                 + str(n_groups) + " kuratierte Gruppe(n) fuer thema='"
                 + str(thema) + "' – Fallback auf Topic-API.")
        else:
            _log("Themen-Guard: Themen-Pool fuer '" + str(thema)
                 + "' leer – direkter Fallback auf Topic-API.")

    # Pfad 1b/2: ohne thema-Filter
    chosen = _pick_seed_v2_aus_kuratisiert(
        rnd, fmt, n_groups, variance, penalized_klang,
        use_embeddings=use_embeddings)

    # Pfad 3: Fallback auffuellen, falls zu wenige kuratierte Gruppen
    if len(chosen) < n_groups:
        fehlt = n_groups - len(chosen)
        _log("Kuratiert reicht nicht (" + str(len(chosen)) + "/"
             + str(n_groups) + ") – auffuellen via API")
        rest = _pick_seed_v2_via_api(rnd, fmt=fmt, thema=thema,
                                     use_embeddings=use_embeddings)
        seen_klang = set(g["klang"] for g in chosen)
        for g in rest:
            if len(chosen) >= n_groups:
                break
            if g["klang"] in seen_klang:
                continue
            chosen.append(g)
            seen_klang.add(g["klang"])

    return chosen


def _pick_seed_v2_via_api(rnd, fmt="AABB-4", thema=None,
                          use_embeddings=USE_EMBEDDINGS_DEFAULT):
    """ALTER Weg: Zieht N Klanggruppen via API (_SEED_WOERTER + _lookup_rhymes).
    Beibehalten fuer thema-gesteuerte Generierung und Fallback falls die
    kuratierten Gruppen fehlen oder nicht ausreichen.
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
            # Embedding-Bonus (BGE-M3): +0.25 auf semantik_score, wenn
            # suchwort<->wort semantisch sehr aehnlich (cosine > 0.75).
            c["semantik_score"], _sim = _embedding_semantic_bonus(
                suchwort, c["wort"], c["semantik_score"],
                use_embeddings=use_embeddings)
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
            if len(sample) >= 8:
                break
        # Falls Pool zu klein, auffuellen
        for c in candidates:
            if c["wort"].lower() not in sampled_set and len(sample) < 8:
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

- HARTE REGEL: Wenn in einer Klanggruppe >= 2 Reimwoerter verfuegbar sind,
  MUSS ein Spruch gebaut werden. Der no_clean_rhyme-Notausgang ist NUR
  erlaubt, wenn KEINES der angebotenen Reimwoerter (auch nicht durch
  sinnvolle Umformulierung) zu einem sauberen Reim fuehrt.
  → NIEMALS doppeln oder Endungen biegen, um die Form zu retten.
  → KEIN "no_clean_rhyme" wegen Bequemlichkeit, nur weil das erste
    Reimwort nicht passt.

- KEIN FAKE-REIM (v21): Wenn von den angebotenen Reimwoertern KEIN
  sauberes Paar phonetisch zueinander passt (z.B. "herein/paragraf"),
  nimm ein ANDERES Reimpaar aus der gleichen Klanggruppe. Du bekommst
  8 Reimwoerter pro Gruppe — probiere mehrere, bis zwei sauber reimen.
  Lieber ein einfacheres aber SAUBERES Reimpaar als ein erzwungener
  Klang-Zusammenklatsch.

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
   VARIATION PFLICHT: die Konsequenz darf NICHT immer Sturz/Ausrutschen sein.
   Wechsle ab: Geruch, Geraeusch, unerwartete Reaktion einer zweiten Figur,
   stiller Moment, peinliche Stille. Slapstick-Sturz hoechstens jeder 4. Spruch.

5a. SHOW, don't tell (HARTE Schreibregel — v21):
   Die Pointe (Zeile 4) muss die Konsequenz als KONKRETES BILD oder
   HANDLUNG zeigen, nie das Ergebnis benennen.
   VERBOTEN (benannte Pointen — Score-Abzug):
   - "dann war die Stimmung im Eimer"
   - "und alle waren sauer"
   - "die Lage war aussichtslos"
   - jedes abstrakte Gefuehl als Pointe
   STATTDESSEN: Was sieht/hoert/riecht/man man in diesem Moment? Ein
   nasser Fleck. Ein Geruch. Eine peinliche Stille. Eine zweite Figur
   mit hochgezogener Augenbraue. Ein quietschender Schuh auf Fliesen.
   Nur was man SIEHT/HOERT/RIECHT zaehlt als Pointe.
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
- Pointen-Variation: NICHT immer Sturz/Ausrutschen — variiere Konsequenz
  (Geruch, Geraeusch, Reaktion einer zweiten Figur, stille Entlarvung)
- Sauberer phonetischer Endreim — kein optischer Reim, kein Füll-Reim
- Gleichmäßiger Rhythmus — sprechbar, ~8 Silben pro Zeile (laut-vorlesen-Test)
- Kein Wort-auf-sich-selbst (Haus/Haus verboten)
- Reim INHALTSTRAGEND (v21): Das Reimwort am Zeilenende muss zum Bild
  der Zeile gehoeren, nicht nur zum Klang. Es ist VERBOTEN, ein Wort
  nur wegen des Reims einzubauen (Fuellreime wie "gelbbraun",
  "Wonne", "rot/blutrot", "Tatze/Glatze" nur der Klang wegen).
  Test: Kann man das Reimwort gegen ein anderes austauschen, ohne
  dass der Witz kaputt geht? Wenn ja, ist es ein Fuellreim — verwerfen.
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


def _format_klanggruppe(g, label):
    """Formatiert eine Klanggruppe inkl. Semantik-Daten (Definition, Synonyme)."""
    seed = g.get("seed", "")
    klang = g.get("klang", "")

    # Format Seed
    seed_info = []
    seed_def = g.get("seed_definition", [])
    if seed_def:
        seed_info.append(f'"{seed_def[0][:120]}"')
    seed_syn = g.get("seed_synonyme", [])
    if seed_syn:
        seed_info.append(f"syn: {', '.join(seed_syn[:4])}")

    seed_str = f" (Seed: {seed}" + (" — " + "; ".join(seed_info) if seed_info else "") + ")"
    lines = [f"{label}: \"{klang}\"{seed_str}"]

    # Format Woerter
    woerter_meta = g.get("woerter_meta", [])
    if not woerter_meta:
        # Fallback falls Meta fehlt
        lines.append("  Verfuegbare Reimwoerter: " + ", ".join(g.get("woerter", [])))
    else:
        for wm in woerter_meta:
            w = wm.get("wort", "")
            w_info = []
            w_def = wm.get("definition", [])
            if w_def:
                w_info.append(f"def: {w_def[0][:120]}")
            w_syn = wm.get("synonyme", [])
            if w_syn:
                w_info.append(f"syn: {', '.join(w_syn[:4])}")

            w_str = f"  - {w}" + (f"  ({'; '.join(w_info)})" if w_info else "")
            lines.append(w_str)

    return "\n".join(lines) + "\n"


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
            _format_klanggruppe(klang_gruppen[0], "Klang-Gruppe A (Zeile 1+2)") + "\n" +
            _format_klanggruppe(klang_gruppen[1], "Klang-Gruppe B (Zeile 3+4)")
        )
    elif fmt == "ABAB-4" and len(klang_gruppen) >= 2:
        gruppen_text = (
            _format_klanggruppe(klang_gruppen[0], "Klang-Gruppe A (Zeile 1+3)") + "\n" +
            _format_klanggruppe(klang_gruppen[1], "Klang-Gruppe B (Zeile 2+4)")
        )
    elif len(klang_gruppen) >= 1:
        # Fallback: nur 1 Gruppe (z.B. AA-2)
        gruppen_text = _format_klanggruppe(klang_gruppen[0], "Klang-Gruppe (alle Zeilen)")
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
- Nutze die Bedeutungen/Synonyme fuer ein konkretes Bild oder ein Wortspiel in der Pointe. Bleib in der angebotenen Klang-Familie und nutze ueberwiegend die angebotenen Reimwoerter. Die Pointe soll etwas WENDEN/zuspitzen, nicht nur das Thema benennen.
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


def _reim_endung(wort):
    """Phonetische Reim-Endung (ab dem letzten Vokal) fuer Fallback-Check.
    Die v12-DB ist nicht vollstaendig — dieser Fallback rettet gueltige Reime,
    die nicht in der Partnerliste stehen.
    J.6: umgestellt von 'letzte 3 Zeichen' auf 'ab letztem Vokal',
    damit -at/-ot/-ut-Familien sauber matchen (spagat/zitat/format -> 'at').
    Konsonanten-Dopplungen werden kollabiert (pott/spot -> 'ot', Bett/Wett -> 'et')."""
    w = wort.lower().strip()
    for alt, neu in [("ä", "e"), ("ö", "e"), ("ü", "e"), ("ß", "ss")]:
        w = w.replace(alt, neu)
    # Reim-Endung = ab dem LETZTEN Vokal (a, e, i, o, u, y) bis Wortende
    endung = w
    for i in range(len(w) - 1, -1, -1):
        if w[i] in "aeiouy":
            endung = w[i:]
            break
    # Konsonanten-Dopplungen kollabieren (tt->t, ll->l, ss->s, ...)
    # nur gleiche Zeichen, nicht "ck" oder "sch"
    endung = re.sub(r"([bcdfghjklmnpqrstvwxz])\1+", r"\1", endung)
    return endung


# ── J.5 IPA-basierter Reim-Check ──────────────────────────────────────────────────────

# IPA-Vokale: lateinische Buchstaben + IPA-Sonderzeichen
_IPA_VOKALE_EINZEL = set("aeiouyəɛɔɐɑɒʏʉɪʊæœøØ")
# 2-Zeichen-Diphthonge (IPA + Umschrift-Varianten)
_IPA_VOKALE_DIGRAPH = {
    "aɪ", "aʊ", "ɔɪ", "ɔʏ", "eɪ", "oʊ", "ɑɪ", "ɑʊ",
    "ae", "oe", "ue", "ai", "au", "oi", "ou", "ei", "eu",
}


def _normalize_ipa(ipa):
    """Entfernt Klammern [], Schraegstriche //, Stress- und Laengenzeichen."""
    s = str(ipa).strip()
    for ch in "[]/":
        s = s.replace(ch, "")
    for ch in "ˈˌˑ":          # primary/secondary/semi stress
        s = s.replace(ch, "")
    s = s.replace("ː", "")    # Laengenzeichen
    return s.strip()


def _ipa_reim_teil(ipa_norm):
    """Reim-Teil ab dem LETZTEN Vokal (Einzelzeichen oder Diphthong)."""
    i = len(ipa_norm)
    while i > 0:
        i -= 1
        if i + 1 < len(ipa_norm) and ipa_norm[i:i + 2] in _IPA_VOKALE_DIGRAPH:
            return ipa_norm[i:]
        if ipa_norm[i] in _IPA_VOKALE_EINZEL:
            return ipa_norm[i:]
    return ipa_norm  # kein Vokal gefunden -> ganzer String als Fallback


def _ipa_reimt(w1, w2, ipa_map):
    """Vergleicht 2 Woerter via IPA. Gibt 'pass', 'fail' oder None (IPA fehlt)."""
    v1 = ipa_map.get(w1.lower())
    v2 = ipa_map.get(w2.lower())
    if not v1 or not v2:
        return None  # IPA fehlt fuer mind. eines -> Fallback-Pfad
    for ip1 in v1:
        for ip2 in v2:
            t1 = _ipa_reim_teil(_normalize_ipa(ip1))
            t2 = _ipa_reim_teil(_normalize_ipa(ip2))
            if t1 and t1 == t2:
                return "pass"
    return "fail"


def _build_ipa_map(klang_gruppen):
    """Baut IPA-Map: wort(lower) -> list[ipa_strings], aus woerter_meta."""
    ipa_map = {}
    if not klang_gruppen:
        return ipa_map
    for g in klang_gruppen:
        for wm in g.get("woerter_meta", []):
            ipa = wm.get("ipa") or []
            if ipa:
                ipa_map[wm.get("wort", "").lower()] = ipa
    return ipa_map


def _check_reimpaar(w1, w2, ipa_map, zeile_label, gruppe_partner=None,
                    reim_strenge="DB-streng"):
    """Prueft ein Reimpaar in 3 Stufen:
    1. DB-Partnerliste (autoritativ): beide Woerter in gruppe_partner -> PASS
    2. IPA (objektiv): beide in ipa_map -> IPA-Endevergleich
    3. _reim_endung (Heuristik): phonetischer Fallback

    Gibt (True, None) bei PASS bzw. (False, reason_str) bei REJECT.
    Telemetrie: zaehlt db_checks / ipa_checks / heuristik_checks.

    Schritt C: reim_strenge steuert NUR Stufe 3 (Heuristik-Fallback).
    - "DB-streng" (Default): Stufe 3 greift wie bisher — phonetischer Fallback
      entscheidet ueber PASS/REJECT.
    - "IPA-tolerant": Stufe 3 wird NICHT als Hard-Reject gewertet. Sobald
      IPA keinen 'fail' liefert, gilt das Paar als bestanden (Toleranz fuer
      kreative Reime). DB-Partnerliste (Stufe 1) bleibt IMMER autoritativ.
    """
    w1l, w2l = w1.lower(), w2.lower()
    # Stufe 1: DB-Partnerliste (autoritativ)
    if gruppe_partner is not None and w1l in gruppe_partner and w2l in gruppe_partner:
        _GEN_STATUS["db_checks"] += 1
        return True, None
    # Stufe 2: IPA (objektiv)
    r = _ipa_reimt(w1, w2, ipa_map)
    if r == "pass":
        _GEN_STATUS["ipa_checks"] += 1
        return True, None
    if r == "fail":
        _GEN_STATUS["ipa_checks"] += 1
        _GEN_STATUS["ipa_mismatches"].append(w1 + "/" + w2)
        return False, "ipa_mismatch: " + zeile_label + " (" + w1 + "/" + w2 + ")"
    # Stufe 3: _reim_endung-Heuristik (Fallback wenn IPA keine klare Aussage liefert)
    _GEN_STATUS["heuristik_checks"] += 1
    if _reim_endung(w1) != _reim_endung(w2):
        _GEN_STATUS["fallback_fails"].append(w1 + "/" + w2)
        if reim_strenge == "IPA-tolerant":
            # Toleranz-Modus: Heuristik-Fail ist kein Hard-Reject mehr.
            # Wir loggen es weiterhin als fallback_fail zur Telemetrie,
            # geben aber (True, None) zurueck — der Spruch darf durch.
            return True, None
        return False, "fallback_fail: " + zeile_label + " (" + w1 + "/" + w2 + ")"
    return True, None


def validate_spruch(spruch_json, klang_gruppen=None, reim_strenge="DB-streng"):
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

    Schritt C: reim_strenge wird an _check_reimpaar durchgereicht und steuert
    nur den Heuristik-Fallback (Stufe 3). DB-Partnerliste bleibt autoritativ.
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
        return False, "identisch: " + str(list(set(dupes)))

    # (b) self_score — NICHT MEHR als Reject-Kriterium (nur Sortier-Kriterium in generate_spruch_best)

    # (c) AUTORITATIVE Reimpruefung gegen die v12-DB — mit phonetischem Fallback (v17)
    #     Die DB ist nicht vollstaendig. Wenn ein Wort nicht in der Partnerliste steht,
    #     pruefen wir den phonetischen Fallback (gleiche Reim-Endung, letzte 2-3 Laute).
    #     Nur wenn auch der Fallback scheitert -> Reject "fallback_fail".
    fmt = spruch_json.get("format", "AABB-4")
    ipa_map = _build_ipa_map(klang_gruppen)
    if klang_gruppen and len(ende) >= 2:
        gA = klang_gruppen[0].get("partner", set()) | {klang_gruppen[0].get("seed", "").lower()}
        if fmt == "ABAB-4" and len(ende) >= 4:
            # Kreuzreim: 1+3 in Gruppe A, 2+4 in Gruppe B
            ok, reason = _check_reimpaar(ende[0], ende[2], ipa_map, "zeile 1/3", gA, reim_strenge=reim_strenge)
            if not ok:
                return False, reason
            if len(klang_gruppen) > 1:
                gB = klang_gruppen[1].get("partner", set()) | {klang_gruppen[1].get("seed", "").lower()}
                ok, reason = _check_reimpaar(ende[1], ende[3], ipa_map, "zeile 2/4", gB, reim_strenge=reim_strenge)
                if not ok:
                    return False, reason
                if ende[0] in gB or ende[1] in gA:
                    return False, "identisch: AAAA-schutz"
        else:
            # Paarreim: 1+2 in Gruppe A, 3+4 in Gruppe B
            ok, reason = _check_reimpaar(ende[0], ende[1], ipa_map, "zeile 1/2", gA, reim_strenge=reim_strenge)
            if not ok:
                return False, reason
            if len(klang_gruppen) > 1 and len(ende) >= 4:
                gB = klang_gruppen[1].get("partner", set()) | {klang_gruppen[1].get("seed", "").lower()}
                ok, reason = _check_reimpaar(ende[2], ende[3], ipa_map, "zeile 3/4", gB, reim_strenge=reim_strenge)
                if not ok:
                    return False, reason
                # AAAA-Schutz: Gruppe A und B duerfen nicht identisch reimen
                if ende[0] in gB or ende[2] in gA:
                    return False, "identisch: AAAA-schutz"

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
            return False, "ruehrend: " + a + "/" + b

    # (e) faule Endungs-Reime
    for a, b in pairs:
        for sfx in REJECT_SUFFIXE:
            if a.endswith(sfx) and b.endswith(sfx):
                return False, "faule_endung: " + a + "/" + b + " (beide -" + sfx + ")"

    # (f) Reimwoerter muessen am Zeilenende stehen
    reim = [_norm(w) for w in spruch_json.get("reimwoerter", [])]
    if reim and set(reim) - set(ende):
        return False, ("reimwoerter nicht am zeilenende: " +
                       str(reim) + " vs " + str(ende))

    # (g) Schlusswort darf kein Abstraktum sein (v15 Sprache-Regel)
    if ende and ende[-1] in ABSTRAKT_BLACKLIST:
        return False, "abstraktes schlusswort: " + ende[-1]

    # (h) Masterformel-Flags ehrlich erzwingen (v15)
    #     kausal = Pflicht-Hard-Reject (Subjekt muss Z1->Z4 verursachen)
    #     letztes_wort_sinnlich = NICHT mehr hard-reject (v17) — das LLM-Flag
    #     ist unzuverlaessig (selbst-deklariert wie der alte self_score).
    #     Der Judge bestraft abstrakte Schlusswoerter via Soft-Penalty (d).
    if not spruch_json.get("kausal", False):
        return False, "keine kausale kette (Z1 verursacht Z4 nicht)"

    # (i) ABAB verlangt Kreuzreim (1<->3, 2<->4), nicht Paarreim
    if spruch_json.get("format") == "ABAB-4" and len(ende) >= 4:
        if ende[0] == ende[1] or ende[2] == ende[3]:
            return False, "ABAB verlangt Kreuzreim, kein Paarreim"

    return True, "ok"


# ── Kern-Funktion ──────────────────────────────────────────────────────────────

def generate_spruch(api_key=None, mode="long", rnd=None, debug=False, model=None,
                    thema=None, drehscheibe=None, derbheit="derb",
                    reim_strenge="DB-streng", fmt_request="gemischt",
                    use_embeddings=USE_EMBEDDINGS_DEFAULT):
    """Generiert einen Bauernspruch mit v2 API-Lookup + System-Prompt.
    thema:       optional – steuert die Seed-Auswahl auf ein semantisches Feld.
    drehscheibe: optionales Dict {figur, setting, twist, thema, form} –
                 gewaehlte Felder werden als harte Vorgaben eingebaut.
    derbheit:    "mild" | "mittel" | "derb" – additiver TON-Block im Prompt.
    reim_strenge:"DB-streng" | "IPA-tolerant" – nur Heuristik-Fallback.
    fmt_request: "AA-2" | "AABB-4" | "ABAB-4" | "gemischt" – Laenge/Form.
    """
    global _DEBUG
    _DEBUG = debug
    _status_reset()
    _GEN_STATUS["running"] = True
    from datetime import datetime
    _GEN_STATUS["started"] = datetime.now().strftime("%H:%M:%S")
    used_model = model or GLM_MODEL
    _GEN_STATUS["model"] = used_model
    _reject_reasons = []  # Telemetrie: Reject-Gründe pro Kandidat

    if rnd is None:
        rnd = random.Random()

    # ── Schritt C: Derbheit normalisieren ──
    if derbheit not in ("mild", "mittel", "derb"):
        derbheit = "derb"
    if reim_strenge not in ("DB-streng", "IPA-tolerant"):
        reim_strenge = "DB-streng"
    if fmt_request not in ("AA-2", "AABB-4", "ABAB-4", "gemischt"):
        fmt_request = "gemischt"

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
         (("' | Drehscheibe: " + str(drehscheibe)) if drehscheibe else "") +
         " | derbheit=" + derbheit + " | reim_strenge=" + reim_strenge)

    # v14: Multi-Klanggruppen-Seed statt einzelnes Seed-Wort
    # v15: ABAB mit ~30% Wahrscheinlichkeit aktivieren
    # Schritt 6: Drehscheibe.form kann fmt fixieren
    # Schritt C: fmt_request (Top-Level) kann fmt ebenfalls fixieren (vor
    # Drehscheibe, damit UI-Wunsch sichtbar bleibt).
    d_form = drehscheibe.get("form", "") if drehscheibe else ""
    if mode == "long":
        if d_form == "AABB" or fmt_request == "AABB-4":
            fmt = "AABB-4"
        elif d_form == "ABAB" or fmt_request == "ABAB-4":
            fmt = "ABAB-4"
        else:
            fmt = rnd.choices(["AABB-4", "ABAB-4"], weights=[70, 30])[0]
    else:
        fmt = "AA-2"
    # Schritt C: fmt_request kann mode short erzwingen
    if fmt_request == "AA-2":
        mode = "short"
        fmt = "AA-2"
    klang_gruppen = _pick_seed_v2(rnd, fmt=fmt, thema=thema,
                                  use_embeddings=use_embeddings)

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
    # Schritt C: Derbheit-Block additiv (vor den dynamischen Beispielen).
    dyn_examples = _build_dynamic_examples(n=3)
    system_prompt_full = (SYSTEM_PROMPT + _build_derbheit_block(derbheit)
                          + dyn_examples)

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
            _reject_reasons.append("json_parse")
            letzter_spruch = antwort
            continue

        # LLM hat kein sauberen Reim gefunden
        if parsed.get("error") == "no_clean_rhyme":
            _log("LLM meldet: kein sauberer Reim — " + str(parsed.get("grund", "")))
            _reject_reasons.append("no_clean_rhyme")
            continue

        spruch_text = parsed.get("spruch", "")
        if not spruch_text:
            _log("Leerer Spruch im JSON")
            _reject_reasons.append("leerer_spruch")
            continue

        letzter_spruch = spruch_text

        # ── Hard-Reject Validator (v14, autoritativ gegen DB) ──
        # Schritt C: reim_strenge steuert nur den Heuristik-Fallback
        valid, reason = validate_spruch(parsed, klang_gruppen=klang_gruppen,
                                        reim_strenge=reim_strenge)
        if not valid:
            # v20: fallback_fail zusaetzlich mit eigenem Reim-Endung-Pair loggen,
            # damit erzwungene Fake-Reime von zu strengen _reim_endung-Checks
            # unterscheidbar sind (das Wortpaar steht schon in reason).
            _log("VALIDIERUNG FEHLGESCHLAGEN: " + reason)
            if reason and reason.startswith("fallback_fail"):
                # reason enthaelt schon "fallback_fail: zeile X/Y (wort1/wort2)"
                _log("REIM-ENDSCHWACH: " + reason.split(": ", 1)[-1])
            _reject_reasons.append(reason)
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
            "reject_reasons": _reject_reasons,
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
        "reject_reasons": _reject_reasons,
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


def _read_deepinfra_api_key() -> str:
    """Liest den DeepInfra API-Key aus ENV oder config.json.

    Reihenfolge:
    1. Environment-Variable DEEPINFRA_API_KEY
    2. config.json unter Schluessel 'deepinfra_api_key'
    3. "" (Rueckfall auf GLM passiert im Dispatcher)
    """
    import os
    key = os.environ.get("DEEPINFRA_API_KEY", "")
    if key:
        return key
    cfg = Path(__file__).parent.parent / "config.json"
    if cfg.exists():
        try:
            return json.load(open(cfg, encoding="utf-8")).get("deepinfra_api_key", "")
        except Exception:
            return ""
    return ""


def get_available_models():
    """Liefert alle verfuegbaren Modelle mit Provider-Info."""
    grok_key = _read_grok_api_key()
    deepinfra_key = _read_deepinfra_api_key()
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
    # DeepInfra Modelle (nur wenn API-Key vorhanden)
    if deepinfra_key:
        for m in DEEPINFRA_FALLBACKS:
            # Anzeigename: letztes Segment nach Slash, Bindestrich als Leerzeichen
            short = m.split("/")[-1]
            models.append({"id": m, "name": short, "provider": "deepinfra"})
    return models


def _categorize_reject(reason):
    """Mapt einen validate_spruch-Reject-Reason auf eine kurze Kategorie.

    v20: 'sonstiges' aufgeloest — no_clean_rhyme (LLM-Selbstabbruch),
    json_parse (LLM-Antwort unparsbar) und leerer_spruch (kein Text)
    haben eigene Buckets, damit die 0c-Haertung messbar wird.
    """
    r = (reason or "").lower()
    if "kausal" in r:
        return "kausal"
    if "sinnlich" in r:
        return "sinnlich"
    if "abstrakt" in r:
        return "abstrakt"
    if "rhythmus" in r or "silb" in r:
        return "rhythmus"
    # Reim-Unterarten (v18 — aufgesplittet)
    if "ipa_mismatch" in r:
        return "ipa_mismatch"
    if "fallback_fail" in r:
        return "fallback_fail"
    if "faule_endung" in r:
        return "faule_endung"
    if "ruehrend" in r:
        return "ruehrend"
    if "identisch" in r:
        return "identisch"
    if "kein_reim" in r:
        return "kein_reim"
    # Pre-LLM-Fehler (v20 — aufgeschluesselt statt "sonstiges")
    if "no_clean_rhyme" in r:
        return "no_clean_rhyme"
    if "json_parse" in r:
        return "json_parse"
    if "leerer_spruch" in r:
        return "leerer_spruch"
    if "kreuzreim" in r or "abab" in r:
        return "format"
    return "sonstiges"


def generate_spruch_best(mode: str = "long", candidates: int = 8,
                         min_score: int = 4, model: str = None,
                         judge_model: str = None, thema: str = None,
                         drehscheibe=None, derbheit: str = "derb",
                         reim_strenge: str = "DB-streng",
                         fmt_request: str = "gemischt",
                         use_embeddings: bool = USE_EMBEDDINGS_DEFAULT) -> dict:
    """High-Level: erzeugt mehrere valide Kandidaten und laesst einen
    separaten Judge-LLM den besten waehlen (Generate-then-rank).

    mode:         "long" (4-Zeiler) | "short" (2-Zeiler)
    candidates:   Anzahl vollstaendig zu erzeugender Kandidaten (KEIN Early-Break)
    min_score:    Mindest-Self-Score, ab dem ein Kandidat in die Judge-Auswahl kommt
    model:        Generier-Modell (z.B. "glm-4.6") oder None fuer Default
    judge_model:  Judge-Modell (Default: JUDGE_MODEL = staerkstes verfuegbares Modell)
    thema:        optional – steuert die Seed-Auswahl auf ein semantisches Feld
    drehscheibe:  optionales Dict {figur, setting, twist, thema, form}
    derbheit:     "mild" | "mittel" | "derb" – additiver TON-Block (Schritt C)
    reim_strenge: "DB-streng" | "IPA-tolerant" – nur Heuristik-Fallback (Schritt C)
    fmt_request:  "AA-2" | "AABB-4" | "ABAB-4" | "gemischt" (Schritt C)
    """
    api_key = _read_api_key()
    if not api_key:
        return {"ok": False, "error": "Kein API-Key (config.json)"}

    used_judge = judge_model or JUDGE_MODEL
    rnd = random.Random()

    # Schritt C: Werte fuer Durchreichung normalisieren
    if derbheit not in ("mild", "mittel", "derb"):
        derbheit = "derb"
    if reim_strenge not in ("DB-streng", "IPA-tolerant"):
        reim_strenge = "DB-streng"
    if fmt_request not in ("AA-2", "AABB-4", "ABAB-4", "gemischt"):
        fmt_request = "gemischt"

    # Drehscheibe als JSON-String normalisieren (fuer Archiv)
    drehscheibe_json = ""
    if drehscheibe:
        if isinstance(drehscheibe, str):
            drehscheibe_json = drehscheibe
        else:
            drehscheibe_json = json.dumps(drehscheibe, ensure_ascii=False)

    _log("Generate-then-rank: " + str(candidates) + " Kandidaten, Judge=" + str(used_judge) +
         ((", Thema=" + str(thema)) if thema else "") +
         ((", Drehscheibe=" + drehscheibe_json) if drehscheibe_json else "") +
         ", derbheit=" + derbheit + ", reim_strenge=" + reim_strenge +
         ", fmt=" + fmt_request)

    valid_pool = []       # valide Sprueche mit self_score >= min_score (Judge-Pool)
    fallback_pool = []    # alle ok-Ergebnisse (falls kein valider dabei ist)
    last_resort_pool = [] # ok:False-Versuche mit nicht-leerem Text (Notfall-Judge-Pool)
    all_attempts = []     # ALLE generate_spruch-Returns (auch komplett gescheiterte) — fuer echte no_clean_rhyme-Rate

    for c in range(int(candidates)):
        if _GEN_STATUS["cancel"]:
            _log("Abgebrochen durch Benutzer (bei Kandidat " + str(c + 1) + ")")
            break
        _log("Kandidat " + str(c + 1) + "/" + str(candidates))
        r = generate_spruch(mode=mode, rnd=rnd, model=model, thema=thema,
                            drehscheibe=drehscheibe, derbheit=derbheit,
                            reim_strenge=reim_strenge, fmt_request=fmt_request,
                            use_embeddings=use_embeddings)
        all_attempts.append(r)  # jedes Ergebnis landet in der vollstaendigen Telemetrie
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

    # ── Reject-Telemetrie: ALLE Versuche aggregieren (inkl. komplett gescheiterte)
    #     So sehen wir die echte no_clean_rhyme-Rate und auch dedup-Konflikte,
    #     die in der Pool-Selektion unter den Tisch fallen wuerden.
    _reject_stats = {}
    for r in all_attempts:
        for reason in r.get("reject_reasons", []):
            cat = _categorize_reject(reason)
            _reject_stats[cat] = _reject_stats.get(cat, 0) + 1
            # v21: unbenannte 'sonstiges'-Rejects mit Roh-String loggen
            if cat == "sonstiges":
                _log("sonstiges-Reject: " + str(reason))
    _log("Reject-Telemetrie ueber " + str(len(all_attempts)) + " Versuche")
    if _reject_stats:
        _reject_summary = ", ".join(
            k + "=" + str(v) for k, v in sorted(_reject_stats.items(),
                                                 key=lambda x: -x[1])
        )
        _log("Rejects: " + _reject_summary)

    if not pool:
        _log("Keine validen Kandidaten erzeugt – liefere besten Fallback")
        return {"ok": False, "error": "kein output"}

    _log(str(len(pool)) + " Kandidaten fuer Judge-Bewertung")
    urteil = _judge_sprueche(pool, model=used_judge, derbheit=derbheit)
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
        # Auto-Favorit + Auto-Veroeffentlichung nur bei
        # judge_score >= 4 UND >= 2 Kandidaten im Judge-Pool
        js = best.get("judge_score", 0)
        try:
            js = float(js)
        except (TypeError, ValueError):
            js = 0.0
        if archiv_id and js >= 4.0 and len(pool) >= 2:
            archive.set_favorit(archiv_id, 1)
            archive.set_veroeffentlicht(archiv_id, 1)
            _log("Auto-Favorit + veroeffentlicht (judge_score=" + str(js) +
                 " >= 4.0, " + str(len(pool)) + " Kandidaten verglichen)")
        elif archiv_id:
            _log("Nur Entwurf — kein Auto-Veroeffentlichen (judge_score=" +
                 str(js) + ", pool=" + str(len(pool)) + ")")
    except Exception as e:
        _log("Archiv-Speicherung fehlgeschlagen: " + str(e))

    # J.5 IPA-Reim-Check Telemetrie ins Ergebnis schreiben
    best["ipa_checks"] = _GEN_STATUS["ipa_checks"]
    best["heuristik_checks"] = _GEN_STATUS["heuristik_checks"]
    best["db_checks"] = _GEN_STATUS["db_checks"]
    best["ipa_mismatches"] = list(_GEN_STATUS["ipa_mismatches"])
    best["fallback_fails"] = list(_GEN_STATUS["fallback_fails"])

    return best


def generate_spruch_v2(mode: str = "long", candidates: int = 8,
                       min_score: int = 4, model: str = None,
                       thema: str = None, drehscheibe=None,
                       derbheit: str = "derb",
                       reim_strenge: str = "DB-streng",
                       fmt_request: str = "gemischt",
                       use_embeddings: bool = USE_EMBEDDINGS_DEFAULT) -> dict:
    """Duenner Wrapper auf generate_spruch_best (Signatur bleibt erhalten).
    Delegiert an den Generate-then-rank-Ablauf mit Judge-Bewertung.
    Schritt C: neue optionale Parameter werden durchgereicht.
    use_embeddings: BGE-M3 Embedding-Bonus (default True; ohne Key No-Op).
    """
    return generate_spruch_best(mode=mode, candidates=candidates,
                                min_score=min_score, model=model,
                                thema=thema, drehscheibe=drehscheibe,
                                derbheit=derbheit, reim_strenge=reim_strenge,
                                fmt_request=fmt_request,
                                use_embeddings=use_embeddings)


def generate_batch(anzahl: int = 3, mode: str = "long", candidates: int = 8,
                   min_score: int = 4, model: str = None,
                   thema: str = None, drehscheibe: str = None,
                   derbheit: str = "derb",
                   reim_strenge: str = "DB-streng",
                   fmt_request: str = "gemischt",
                   use_embeddings: bool = USE_EMBEDDINGS_DEFAULT) -> list:
    """Erzeugt mehrere fertige Sprueche auf einmal.

    anzahl:       Anzahl fertiger Sprueche (jeder einzeln gejudged + archiviert)
    candidates:   Qualitaetsversuche PRO Spruch (nicht Anzahl Ergebnisse!)
    drehscheibe:  optionales Tag fuer das Archiv
    derbheit/reim_strenge/fmt_request: Schritt C — durchgereicht.

    Gibt eine Liste von Ergebnis-Dicts zurueck. Jeder Spruch wird beim
    Durchlauf durch generate_spruch_best automatisch im Archiv gespeichert.
    """
    anzahl = max(1, min(int(anzahl), 20))
    ergebnisse = []
    _log("Batch-Generierung: " + str(anzahl) + " Sprueche" +
         ((", Thema=" + str(thema)) if thema else "") +
         ((", Drehscheibe=" + str(drehscheibe)) if drehscheibe else "") +
         ", derbheit=" + str(derbheit) + ", fmt=" + str(fmt_request))

    for i in range(anzahl):
        if _GEN_STATUS["cancel"]:
            _log("Batch abgebrochen bei Spruch " + str(i + 1) + "/" + str(anzahl))
            break
        _log("=== Batch-Spruch " + str(i + 1) + "/" + str(anzahl) + " ===")
        r = generate_spruch_best(mode=mode, candidates=candidates,
                                 min_score=min_score, model=model,
                                 thema=thema, drehscheibe=drehscheibe,
                                 derbheit=derbheit, reim_strenge=reim_strenge,
                                 fmt_request=fmt_request,
                                 use_embeddings=use_embeddings)
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
