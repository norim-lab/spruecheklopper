import json
import time
import os
import asyncio
import requests
import aiohttp
from pathlib import Path

API_URL = "https://api.z.ai/api/paas/v4/chat/completions"
BATCH_SIZE = 15

CACHE_FILE = Path(__file__).parent / "output" / "wort_cache.json"

_wort_cache = {}


def safe_score(val):
    try:
        s = int(val)
        return max(0, min(5, s))
    except (ValueError, TypeError):
        return 0


def load_cache():
    global _wort_cache
    if CACHE_FILE.exists():
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            _wort_cache = json.load(f)
        print(f"Cache geladen: {len(_wort_cache)} Wörter")


def save_cache():
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(_wort_cache, f, ensure_ascii=False)
    print(f"Cache gespeichert: {len(_wort_cache)} Wörter")
    print(f"String-Konvertierungen: {_string_conversions}")
    print(f"Ungültige Scores: {_invalid_scores}")


load_cache()

KONTEXT_BLACKLIST = [
    "traktor", "motor", "maschine", "schrott", "strom", "elektro",
    "computer", "handy", "internet", "wifi", "auto", "bus", "bahn",
    "flugzeug", "smartphone", "laptop", "monitor", "drucker", "router"
]

_KONTEXT_FALLBACK = [
    "umgekippter Melkeimer",
    "offenes Scheunentor",
    "frischer Misthaufen",
    "tropfender Milcheimer",
    "schmutzige Stallstiefel"
]

_DEFAULT_FARM_OBJECTS = [
    "Mistgabel",
    "Melkeimer",
    "Heuboden",
    "Stalllaterne",
    "Dreschflegel"
]

_VISUAL_HINTS = ["offen", "nass", "alt", "leer", "voll", "verrostet", "umgekippt"]

_VALID_WORD_WHITELIST = {
    "mistgabel", "melkeimer", "heuboden", "stalllaterne", "dreschflegel",
    "scheunentor", "scheunentür", "butterfass", "bauernhaus", "heugabel",
    "sense", "sichel", "garbe", "scheffel", "pflug", "schaufel",
    "wasserfass", "leinenbeutel", "eichenfass", "schwarzbrot", "futtereimer",
    "tränkeimer", "krippenbrett", "holzpflug", "leinenhemd", "brotlaib",
    "melkstuhl", "futterkrippe", "kuhglocke", "wagenschelle", "holzschemel",
    "erdäpfelkorb", "kornschaufel", "holzspaten"
}

_FAKE_WORD_ENDINGS = ("atz", "atern", "tatern", "tz")
_FAKE_WORD_PARTS = ("ungen", "atern", "tatern")
_GENERIC_WEAK_WORDS = {
    "bein", "ding", "machen", "treiben"
}
_STRONG_MARKERS = (
    "arsch", "brust", "hals", "huf", "kot", "mist", "mund", "rumpf",
    "schwanz", "schwein", "sau", "hund", "speck", "dreck", "wurst",
    "durst", "huren", "scheiß", "spei", "spuck", "blut", "zahn", "hahn"
)
_WEAK_RHYME_ENDINGS = ("ock", "und", "aus")
_KNOWN_WORD_BASES = (
    "specht", "knecht", "magd", "hahn", "zahn", "kahn", "bock", "stock",
    "schwein", "hund", "dreck", "rohr", "huf", "ohr", "teich", "bein",
    "stein", "mund", "bund", "lust", "brust", "wust", "ruf", "stumpf",
    "sumpf", "rumpf", "wurst", "durst", "nacht", "tracht", "hecht", "recht",
    "brot", "haus", "korb", "fass", "eimer", "gabel", "pflug", "boden",
    "laterne", "flegel", "tor", "krug", "heu", "mist", "kuh", "wagen",
    "laib", "rad", "beutel", "hemd", "stiefel", "speck", "leck", "schleck"
)


def is_valid_word(wort):
    if not isinstance(wort, str):
        return False

    word = wort.strip()
    lower = word.lower()

    if not lower:
        return False

    if lower in _VALID_WORD_WHITELIST:
        return True

    if len(lower) < 3 or len(lower) > 12:
        return False

    if not word.isalpha():
        return False

    if any(lower.endswith(ending) for ending in _FAKE_WORD_ENDINGS):
        return False

    if any(part in lower for part in _FAKE_WORD_PARTS):
        return False

    if len(lower) > 8 and not any(base in lower for base in _KNOWN_WORD_BASES):
        return False

    return True


def is_generic_weak_word(wort):
    if not isinstance(wort, str):
        return False
    return wort.strip().lower() in _GENERIC_WEAK_WORDS


def levenshtein_distance(a, b):
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) > len(b):
        a, b = b, a

    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            insert_cost = current[j - 1] + 1
            delete_cost = previous[j] + 1
            replace_cost = previous[j - 1] + (ca != cb)
            current.append(min(insert_cost, delete_cost, replace_cost))
        previous = current
    return previous[-1]


def contains_strong_marker(wort):
    if not isinstance(wort, str):
        return False
    lower = wort.strip().lower()
    if not lower:
        return False

    marker_hits = [marker for marker in _STRONG_MARKERS if marker in lower]
    if not marker_hits:
        return False

    if len(lower) >= 7:
        return True

    return any(len(lower) > len(marker) + 1 for marker in marker_hits)


def _is_one_edit_apart(a, b):
    return levenshtein_distance(a, b) <= 1


def has_unexpected_or_derb_contrast(suchwort, wort, derb=False, score=None):
    if not isinstance(suchwort, str) or not isinstance(wort, str):
        return False

    sw = suchwort.strip().lower()
    ww = wort.strip().lower()
    if not sw or not ww:
        return False

    # Starke Wörter mit hoher Pointe immer behalten.
    if score is not None and score >= 4:
        return True

    # Derb ist nur noch bei hoher Pointe automatisch ausreichend.
    if derb and score is not None and score >= 4:
        return True

    if sw == ww or sw in ww or ww in sw:
        return False

    # Score <= 3: nur noch mit klarer Bild-/Marker-Qualität.
    if score is not None and score <= 3:
        if len(ww) <= 5:
            return False
        if levenshtein_distance(sw, ww) <= 1:
            return False
        if ww.endswith(_WEAK_RHYME_ENDINGS):
            return False
        if contains_strong_marker(ww):
            return True
        return False

    # Unsicherheit kippt immer zu Qualität.
    return False


def get_config():
    config_path = Path("config.json")
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"api_key": os.environ.get("ZHIPUAI_API_KEY", ""), "model": "glm-4.7-flashx"}


def _build_headers():
    config = get_config()
    api_key = config.get("api_key")
    if not api_key:
        print("WARNUNG: ZhipuAI API Key fehlt!")
        return None, None
    return api_key, {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }


def call_glm_api(prompt, model_override=None):
    config = get_config()
    api_key = config.get("api_key")
    model = model_override or config.get("model", "glm-4.7-flashx")

    if not api_key:
        return None

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    data = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 300,
        "response_format": {"type": "json_object"}
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(API_URL, headers=headers, json=data, timeout=60)
            if response.status_code == 429:
                wait = (attempt + 1) * 1
                print(f"  429 Rate-Limit, warte {wait}s...")
                time.sleep(wait)
                continue
            response.raise_for_status()
            result = response.json()
            return result["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"GLM API Fehler (Versuch {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 1
                print(f"  Warte {wait}s vor Retry...")
                time.sleep(wait)
    return None


async def call_glm_api_async(session, prompt, model_override=None, semaphore=None, messages=None):
    config = get_config()
    api_key = config.get("api_key")
    model = model_override or config.get("model", "glm-4.7-flashx")

    if not api_key:
        return None

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    data = {
        "model": model,
        "messages": messages if messages else [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 300,
        "response_format": {"type": "json_object"}
    }

    async def _do_call():
        max_retries = 8
        for attempt in range(max_retries):
            try:
                async with session.post(API_URL, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    if resp.status == 429:
                        wait = (attempt + 1) * 2
                        print(f"  429 Rate-Limit, warte {wait}s...")
                        await asyncio.sleep(wait)
                        continue
                    resp.raise_for_status()
                    result = await resp.json()
                    return result["choices"][0]["message"]["content"]
            except Exception as e:
                print(f"  Async API Fehler (Versuch {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep((attempt + 1) * 2)
        return None

    if semaphore:
        async with semaphore:
            return await _do_call()
    return await _do_call()


def _cache_lookup(woerter):
    cached = []
    uncached = []
    for w in woerter:
        key = w.lower()
        if key in _wort_cache:
            cached.append(_wort_cache[key].copy())
        else:
            uncached.append(w)
    return cached, uncached


_string_conversions = 0
_invalid_scores = 0


def _cache_store(bewertungen):
    global _string_conversions, _invalid_scores
    for b in bewertungen:
        if not isinstance(b, dict):
            continue
        if not isinstance(b.get("wort"), str):
            continue
        if not isinstance(b.get("laendlich"), bool):
            b["laendlich"] = False
        if not isinstance(b.get("derb"), bool):
            b["derb"] = False
        key = b.get("wort", "").lower()
        if key:
            raw = b.get("pointe_score")
            if raw is None:
                raw = 5 if b.get("pointe") else 0
            ps = safe_score(raw)
            if raw is not None and not isinstance(raw, int):
                _string_conversions += 1
            if raw is not None and safe_score(raw) == 0 and str(raw).strip() not in ("0", "0.0", ""):
                _invalid_scores += 1
            _wort_cache[key] = {
                "wort": b["wort"],
                "laendlich": bool(b.get("laendlich", False)),
                "derb": bool(b.get("derb", False)),
                "pointe_score": ps,
            }


def _parse_json_response(response_text):
    if not response_text:
        return None
    try:
        data = json.loads(response_text)

        if isinstance(data, list):
            return data

        if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
            return data["data"]

        print("WARN: Unerwartetes JSON Format")
        return None

    except json.JSONDecodeError:
        print(f"JSON Parse Fehler: {response_text[:100]}...")
        return None


EVAL_PROMPT = """Du bewertest deutsche Wörter für freche Bauernsprüche.
Thema: "{thema}"

Für jedes Wort vergibst du zwei Flags und einen Score:
- laendlich (true/false): passt in Hofwelt (Tier, Feld, Stall, Natur, Werkstatt, Küche, Garten)?
  WICHTIG – laendlich:true gilt auch für:
  - Waldtiere und Wildtiere: Specht, Fuchs, Hase, Reh, Dachs, Eule, Falke
  - Komposita mit Tierbezug: Baumspecht, Rotspecht, Grauspecht, Jungspecht, Grünspecht, Buntspecht
  - Tätigkeiten auf dem Dorf: zechen (Bauernfest), feiern, tanzen, mähen, dreschen
  - Adjektive die Bauernleben beschreiben: schlecht, recht, echt (nutzbar im Vers!)
  - Werkzeug und Handwerk: Zange, Säge, Hammer, Meißel
  - Körperteile im ländlichen Kontext: Wange, Zahn, Knie, Schulter
  - Naturbegriffe: Schlange, Stange, Fange, Gange
- derb (true/false): Körper, Dreck, Tier-Konnotation oder vulgär andeutbar?
- pointe_score (0-5): Wie gut funktioniert das Wort als LETZTES WORT eines frechen Reims?

POINTE_SCORE REGELN:
5 = eindeutige Doppeldeutigkeit, sitzt sofort
    Beispiele: Spritzgurke, Rammel, brecht, Schwanz
4 = starke Komik, kurz, überraschend
    Beispiele: Hecht, Magd, Knecht, Gurke
3 = funktioniert als Pointe, solide
    Beispiele: Baumspecht, Jagd, Mist
2 = schwaches Potenzial
    Beispiele: Grauspecht, Jagdrecht
1 = kaum nutzbar
0 = keine Pointe möglich
    Beispiele: Recht, Presserecht, Newsletter

WICHTIG: Wörter mit Körper/Tier/Werkzeug-Doppeldeutigkeit bekommen mindestens 3.

BEISPIELE laendlich:true (oft falsch bewertet!):
Specht, Rotspecht, Baumspecht, Grauspecht, Jungspecht, Grünspecht, Buntspecht,
Zange (Werkzeug), Wange (Körper), Schlange (Tier), Stange (Hof), zecht (Bauernfest),
schlecht (als Adjektiv im Vers), Salzgurke, Senfgurke, Dillgurke, Salatgurke,
Hecht (Fisch/Teich), Knecht (Hof), recht (als Adjektiv), echt (als Adjektiv)

Wörter: {woerter}

Antworte AUSSCHLIESSLICH mit einem JSON-Array im folgenden Format:
[
  {{"wort": "Beispielwort", "laendlich": true, "derb": false, "pointe_score": 4}}
]
"""


def evaluate_woerter(woerter_array, thema, model_override=None):
    cached_results, uncached = _cache_lookup(woerter_array)
    if not uncached:
        return cached_results

    prompt = EVAL_PROMPT.format(thema=thema, woerter=json.dumps(uncached, ensure_ascii=False))
    response_text = call_glm_api(prompt, model_override=model_override)

    data = _parse_json_response(response_text)
    if data is None:
        fallback = [{"wort": w, "laendlich": False, "derb": False, "pointe_score": 0} for w in uncached]
        _cache_store(fallback)

        cleaned = []
        for b in fallback:
            key = b["wort"].lower()
            if key in _wort_cache:
                cleaned.append(_wort_cache[key].copy())

        return cached_results + cleaned

    _cache_store(data)

    cleaned = []
    for b in data:
        key = b.get("wort", "").lower()
        if key in _wort_cache:
            cleaned.append(_wort_cache[key].copy())

    return cached_results + cleaned


async def evaluate_woerter_async(session, woerter_array, thema, model_override=None, semaphore=None):
    cached_results, uncached = _cache_lookup(woerter_array)
    if not uncached:
        return cached_results

    prompt = EVAL_PROMPT.format(thema=thema, woerter=json.dumps(uncached, ensure_ascii=False))
    response_text = await call_glm_api_async(session, prompt, model_override=model_override, semaphore=semaphore)

    data = _parse_json_response(response_text)
    if data is None:
        fallback = [{"wort": w, "laendlich": False, "derb": False, "pointe_score": 0} for w in uncached]
        _cache_store(fallback)

        cleaned = []
        for b in fallback:
            key = b["wort"].lower()
            if key in _wort_cache:
                cleaned.append(_wort_cache[key].copy())

        return cached_results + cleaned

    _cache_store(data)

    cleaned = []
    for b in data:
        key = b.get("wort", "").lower()
        if key in _wort_cache:
            cleaned.append(_wort_cache[key].copy())

    return cached_results + cleaned


def _filter_kontext(data, suchwort=""):
    if not isinstance(data, list):
        return []

    filtered = []
    seen = set()

    for item in data:
        if not isinstance(item, str):
            continue

        word = item.strip()
        lower = word.lower()

        if suchwort:
            such_lower = suchwort.lower()
            if any([
                len(such_lower) >= 2 and lower.endswith(such_lower[-2:]),
                len(such_lower) >= 3 and lower.endswith(such_lower[-3:]),
                such_lower in lower
            ]):
                continue

        if any(b in lower for b in KONTEXT_BLACKLIST):
            continue

        if lower.endswith("heit") or lower.endswith("keit") or lower.endswith("ung"):
            continue

        if lower in ["arbeit", "leben", "ding", "sache", "bereich"]:
            continue

        if lower in ["stall", "feld", "hof"]:
            continue

        if any(x in lower for x in ["ungen", "ahn", "all", "ing", "ling"]):
            continue

        if not is_valid_word(word):
            continue

        if lower in seen:
            continue

        seen.add(lower)
        filtered.append(word)

    return filtered[:5]


def generate_kontext(suchwort, thema, model_override=None):
    prompt = f"""Erzeuge GENAU 5 Begriffe. Jeder Begriff muss ein klares visuelles Objekt aus dem bäuerlichen Alltag um 1890 sein (z. B. 'Mistgabel', 'Melkeimer').

NUR echte deutsche Begriffe.

VERBOTEN:
* abstrakte Begriffe
* generische Wörter wie Stall, Feld, Hof
* moderne Dinge
* Wörter, die sich auf das Suchwort reimen
* Wörter mit ähnlichem Klang
* Wortvariationen (z. B. Kuhungen, Stallall)
* unsinnige oder erfundene Begriffe
* Wortlisten oder Variationen

JEDES Wort muss ein klares Bild erzeugen.

Thema: {thema}
Suchwort: {suchwort}

Nur JSON-Array zurück:
["wort1","wort2","wort3","wort4","wort5"]"""

    for attempt in range(2):
        response_text = call_glm_api(prompt, model_override=model_override)
        data = _parse_json_response(response_text)
        if data is None:
            if attempt == 0:
                time.sleep(1)
                continue
            return _DEFAULT_FARM_OBJECTS[:]

        filtered = _filter_kontext(data, suchwort=suchwort)

        if len(filtered) < 5:
            if attempt == 0:
                time.sleep(1)
                continue
            return _DEFAULT_FARM_OBJECTS[:]

        return filtered[:5]

    return _DEFAULT_FARM_OBJECTS[:]


def generate_kontext_klang(klang, thema, model_override=None):
    prompt = f"""Erzeuge GENAU 5 Begriffe. Jeder Begriff muss ein klares visuelles Objekt aus dem bäuerlichen Alltag um 1890 sein (z. B. 'Mistgabel', 'Melkeimer').

NUR echte deutsche Begriffe.

VERBOTEN:
* abstrakte Begriffe
* generische Wörter wie Stall, Feld, Hof
* moderne Dinge
* Wörter, die sich auf die Silbe "-{klang}" reimen
* Wörter mit ähnlichem Klang
* Wortvariationen (z. B. Kuhungen, Stallall)
* unsinnige oder erfundene Begriffe
* Wortlisten oder Variationen

JEDES Wort muss ein klares Bild erzeugen.

Thema: {thema}

Nur JSON-Array zurück:
["wort1","wort2","wort3","wort4","wort5"]"""

    for attempt in range(2):
        response_text = call_glm_api(prompt, model_override=model_override)
        data = _parse_json_response(response_text)
        if data is None:
            if attempt == 0:
                time.sleep(1)
                continue
            return _DEFAULT_FARM_OBJECTS[:]

        filtered = _filter_kontext(data, suchwort=klang)

        if len(filtered) < 5:
            if attempt == 0:
                time.sleep(1)
                continue
            return _DEFAULT_FARM_OBJECTS[:]

        return filtered[:5]

    return _DEFAULT_FARM_OBJECTS[:]


async def generate_kontext_suchwort_async(session, suchwort, thema, model_override=None, semaphore=None):
    messages = [
        {"role": "system", "content": "Du bist Experte für deutschen Bauernalltag des 19. Jahrhunderts."},
        {"role": "user", "content": f"""Erzeuge GENAU 5 Begriffe. Jeder Begriff muss ein klares visuelles Objekt aus dem bäuerlichen Alltag um 1890 sein (z. B. 'Mistgabel', 'Melkeimer').

NUR echte deutsche Begriffe.

VERBOTEN:
* abstrakte Begriffe
* generische Wörter wie Stall, Feld, Hof
* moderne Dinge
* Wörter, die sich auf das Suchwort reimen
* Wörter mit ähnlichem Klang
* Wortvariationen (z. B. Kuhungen, Stallall)
* unsinnige oder erfundene Begriffe
* Wortlisten oder Variationen

JEDES Wort muss ein klares Bild erzeugen.

Thema der Reimgruppe: {thema}
Suchwort: {suchwort}

Nur JSON-Array zurück:
["wort1","wort2","wort3","wort4","wort5"]"""}
    ]
    response_text = await call_glm_api_async(session, prompt=None, model_override=model_override, semaphore=semaphore, messages=messages)
    data = _parse_json_response(response_text)
    if data is None:
        return _DEFAULT_FARM_OBJECTS[:]

    filtered = _filter_kontext(data, suchwort=suchwort)

    if len(filtered) < 5:
        return _DEFAULT_FARM_OBJECTS[:]

    return filtered[:5]


def chunk_list(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def process_groups(input_file, output_file):
    input_path = Path(input_file)
    if not input_path.exists():
        print(f"Eingabedatei {input_file} nicht gefunden!")
        return

    processed_keys = set()
    output_path = Path(output_file)
    if output_path.exists():
        with open(output_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        data = json.loads(line)
                        processed_keys.add(f"{data.get('klang')}_{data.get('suchwort')}")
                    except:
                        pass

    groups = []
    with open(input_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                groups.append(json.loads(line))

    print(f"Starte Evaluierung für {len(groups)} Gruppen (Bereits erledigt: {len(processed_keys)})...")

    with open(output_path, "a", encoding="utf-8") as f:
        for i, gruppe in enumerate(groups):
            key = f"{gruppe.get('klang')}_{gruppe.get('suchwort')}"
            if key in processed_keys:
                continue

            woerter = gruppe.get("woerter", [])
            thema = gruppe.get("thema", "")
            suchwort = gruppe.get("suchwort", "")

            print(f"[{i+1}/{len(groups)}] Verarbeite Gruppe: {suchwort} (Klang: {gruppe.get('klang')}) mit {len(woerter)} Wörtern")

            bewertete_woerter = []
            for chunk in chunk_list(woerter, BATCH_SIZE):
                print(f"  -> Sende Batch von {len(chunk)} Wörtern an API...")
                bewertet = evaluate_woerter(chunk, thema)
                bewertete_woerter.extend(bewertet)
                time.sleep(1)

            unique = {}
            for w in bewertete_woerter:
                key = w["wort"].lower()
                if key not in unique or safe_score(w["pointe_score"]) > safe_score(unique[key]["pointe_score"]):
                    unique[key] = w
            bewertete_woerter = list(unique.values())

            print(f"  -> Generiere Kontext für {suchwort}...")
            kontext = generate_kontext(suchwort, thema)

            neue_gruppe = {
                "klang": gruppe.get("klang"),
                "suchwort": suchwort,
                "silben": gruppe.get("silben"),
                "thema": thema,
                "kontext": kontext,
                "woerter": bewertete_woerter
            }

            f.write(json.dumps(neue_gruppe, ensure_ascii=False) + "\n")
            f.flush()

            save_cache()

            time.sleep(2)

    save_cache()
    print(f"Evaluierung abgeschlossen. Ergebnis in {output_file} gespeichert.")


if __name__ == "__main__":
    config = get_config()
    if not config.get("api_key"):
        print("ACHTUNG: ZhipuAI API Key fehlt!")
    else:
        process_groups("output/reimgruppen_raw.jsonl", "output/reimgruppen.jsonl")
