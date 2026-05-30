import requests
import json
import re
import time
import random
import sys
import os
from datetime import datetime
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Wir importieren die KI-Funktionen aus der neuen Datei
try:
    from ai_evaluator import (
        evaluate_woerter, generate_kontext, chunk_list, BATCH_SIZE,
        safe_score, is_valid_word, is_generic_weak_word,
        has_unexpected_or_derb_contrast
    )
except ImportError:
    # Fallback-Dummies, falls die Datei fehlt
    def evaluate_woerter(w, t): return [{"wort": x, "laendlich": False, "derb": False, "pointe": False} for x in w]
    def generate_kontext(s, t): return []
    def chunk_list(l, n): return [l[i:i+n] for i in range(0, len(l), n)]
    def is_valid_word(w): return isinstance(w, str) and bool(w.strip())
    def is_generic_weak_word(w): return False
    def has_unexpected_or_derb_contrast(s, w, derb=False, score=None): return bool(derb)
    BATCH_SIZE = 20

BASE_URL = "https://www.lyrikecke.de/reimlexikon"
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

SESSION_FILE = OUTPUT_DIR / "progress.json"
RESULTS_FILE = OUTPUT_DIR / "reimgruppen.jsonl"
JSON_EXPORT_FILE = OUTPUT_DIR / "reimgruppen.json"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]

REFERERS = [
    "https://www.google.com/",
    "https://www.bing.com/",
    "https://duckduckgo.com/",
    "https://www.lyrikecke.de/",
    "https://www.lyrikecke.de/reimlexikon",
]

THEMEN_WOERTER = {
    "-ECHT": ["knecht", "schlecht", "recht", "gerecht", "verzicht", "geschlecht"],
    "-AGD": ["magd", "jagd", "pfad", "grad"],
    "-AHN": ["hahn", "bahn", "zahn", "kran", "kahn", "wahn", "ahnung"],
    "-OCK": ["bock", "stock", "rock", "block", "flock"],
    "-ALL": ["stall", "hall", "knall", "fall", "ball", "schwall", "wall", "schall"],
    "-ELD": ["feld", "geld", "held", "wald", "gewalt", "bald", "kalt", "halt"],
    "-OT": ["brot", "not", "tod", "gebot", "rot", "spott", "klot"],
    "-EIN": ["schwein", "wein", "stein", "bein", "rein", "klein", "mein", "sein", "hain"],
    "-UT": ["wut", "blut", "mut", "hut", "lust", "brust"],
    "-AFT": ["kraft", "schaft", "luft", "kunst", "gunst", "haft"],
    "-UND": ["hund", "bund", "grund", "mund", "kund", "rund", "stund", "wund", "pfund"],
    "-ECK": ["dreck", "steck", "fleck", "reck", "schmeck"],
    "-USS": ["kuss", "gruss", "fluss", "schluss", "fuss", "genuss", "verdruss"],
    "-UNG": ["jung", "sprung", "zung", "lung", "drung"],
    "-EIT": ["zeit", "weit", "streit", "schreit", "leid", "neid", "schneid"],
    "-EHN": ["sehn", "gehn", "stehn", "wehn", "dehn", "mehrn"],
    "-OHN": ["lohn", "sohn", "thron", "kron", "wohn"],
    "-OHR": ["rohr", "chor", "moor", "bohr", "tor"],
    "-EHR": ["mehr", "wehr", "hehr", "zehr", "lehr", "ehr"],
    "-EIS": ["reis", "eis", "preis", "kreis", "heiß", "weiß", "scheiß"],
    "-AUS": ["haus", "maus", "laus", "kraus", "strauss", "schmaus", "raus", "aus"],
    "-AUM": ["baum", "raum", "traum", "saum", "zaum"],
    "-EUF": ["ruf", "huf", "schuf", "gruf"],
    "-OLZ": ["holz", "stolz", "solz"],
    "-ORN": ["korn", "dorn", "born", "horn", "morgen", "zorn"],
    "-ART": ["art", "fahrt", "wart", "start", "hart", "gart"],
    "-ORT": ["ort", "wort", "fort", "port", "sort", "hort", "mord", "bord"],
    "-URB": ["kurz", "sturz", "furz", "schurz"],
    "-ERK": ["werk", "berg", "stark", "mark", "kerk"],
    "-ECH": ["dach", "mach", "wach", "sprach", "nach", "schwach"],
    "-ICH": ["reich", "lich", "weich", "leich", "mich", "dich"],
    "-OCH": ["doch", "noch", "loch", "koch", "moch"],
    "-UCH": ["buch", "tuch", "fluch", "spruch", "such"],
    "-UCK": ["glück", "stück", "ruck", "druck", "blick", "strick"],
    "-AMP": ["camp", "dampf", "stampf", "lamp", "rampf"],
    "-UMP": ["pump", "rump", "stumpf", "hump"],
    "-ANK": ["bank", "dank", "rank", "tank", "blank", "sank", "trank"],
    "-INK": ["schlank", "blank", "drank", "gesang", "klang"],
    "-UNK": ["trunk", "stunk", "gesang", "spunk"],
    "-ANG": ["gang", "klang", "sang", "stang", "wang", "drang"],
    "-ING": ["ring", "ding", "spring", "bring", "sing", "kling", "schwing"],
    "-ENG": ["eng", "peng", "streng", "lang"],
    "-EICHT": ["nicht", "licht", "sicht", "wicht", "pflicht", "berichtet"],
    "-EIG": ["steig", "neig", "schweig", "zeig"],
    "-EIF": ["reif", "streif", "meist"],
    "-EIB": ["leib", "weib", "reib", "treib", "bleib"],
    "-IER": ["bier", "stier", "hier", "vier", "tier"],
    "-OR": ["tor", "chor", "torf", "vor"],
    "-UR": ["nur", "spur", "kur", "schur", "natur"],
    "-EEL": ["schnell", "hell", "quell", "stell", "zell", "grell"],
    "-IEL": ["gefühl", "spiel", "viel", "ziel", "stiel"],
    "-OSEN": ["rosen", "hosen", "losen", "dosen"],
    "-ACK": ["sack", "pack", "back", "hack", "schmack"],
    "-AR": ["jahr", "haar", "paar", "baar", "schar", "wahr", "gefahr", "nah"],
    "-ÜSS": ["grüß", "müß", "küss", "schüss"],
    "-ABEN": ["graben", "haben", "knaben", "wagen", "sagen", "tragen", "schlagen"],
    "-ADEN": ["laden", "schaden", "faden", "baden", "gnaden"],
    "-AGEN": ["wagen", "sagen", "tagen", "tragen", "schlagen", "ragen", "klagen"],
    "-ATZ": ["satz", "platze", "ratz", "schatz", "latz", "spatz"],
    "-ÜTZ": ["stütze", "schütze", "grütze", "mütze", "sitze", "hitze"],
    "-ITZ": ["spitze", "blitze", "witze", "kitz", "ritze"],
    "-ÜCK": ["rück", "glück", "stück", "blick", "drück"],
    "-AHL": ["zahl", "mahl", "kahl", "strahl"],
    "-AT": ["tat", "stadt", "rat", "spat", "brat"],
    "-EN": ["rennen", "kennen", "nennen", "brennen", "denken"],

    "-IEBE": ["liebe", "triebe", "diebe", "hiebe", "bliebe", "riebe", "schriebe"],
    "-EIZEN": ["reizen", "beizen", "heizen", "geizen", "schmeißen"],
    "-ERZEN": ["herzen", "scherzen", "schmerzen", "kerzen", "stürzen"],
    "-USSEN": ["küssen", "müssen", "wissen", "rissen", "bissen", "flüssen"],
    "-UGEN": ["augen", "taugen", "saugen", "raugen"],
    "-ARMEN": ["armen", "erbarmen", "wärmen", "lärmen"],
    "-EIGEN": ["zeigen", "steigen", "neigen", "reigen", "schweigen", "beugen"],

    "-UST": ["lust", "brust", "wust", "bewusst"],
    "-UNGEN": ["zungen", "lungen", "schwingen", "rungen", "drungen"],
    "-ITZEN": ["sitzen", "schwitzen", "blitzen", "nützen", "stützen", "witzen"],
    "-EIBEN": ["treiben", "reiben", "bleiben", "schreiben", "weiben"],
    "-OCKEN": ["locken", "bocken", "stocken", "hocken", "zocken"],
    "-OTEN": ["bieten", "roten", "noten", "boten", "verboten", "geboten"],

    "-INTEN": ["hinten", "tinte", "kinte"],
    "-URKE": ["gurke", "türke", "würge", "lurke"],
    "-TANGE": ["stange", "lange", "schlange", "zange", "wange"],
    "-ECHER": ["becher", "stecher", "frecher", "brecher", "zecher"],
    "-UMPFE": ["strümpfe", "rümpfe", "stümpfe", "sümpfe", "trümpfe"],
    "-OCKEL": ["sockel", "nockel", "dockel"],
    "-EITER": ["leiter", "weiter", "breiter", "reiter", "streiter"],
    "-ARSH": ["arsche", "marsche", "barsche"],
    "-URST": ["wurst", "durst", "zuerst"],
    "-EIFEN": ["greifen", "pfeifen", "reifen", "schleifen", "streifen", "schweifen"],
    "-ACKEN": ["packen", "hacken", "backen", "wacken", "zacken", "knacken"],
    "-ATZEN": ["kratzen", "tatzen", "platzen", "matzen", "schnatzen"],
    "-AUFEN": ["saufen", "raufen", "laufen", "kaufen", "taufen"],
    "-UMPEN": ["lumpen", "pumpen", "stumpen"],

    "-ÄHNE": ["hähne", "zähne", "wähne", "drähne", "mähne"],
    "-OCKELN": ["bockeln", "hockeln", "lockeln"],
    "-ULLE": ["bulle", "fülle", "hülle", "mülle"],
    "-ÄHRE": ["fähre", "mähre", "zähre", "gebähre"],
    "-AMMEL": ["hammel", "gammel", "rammel", "jammel", "sammel", "mammel"],
    "-AUBER": ["tauber", "zauber", "schlauber"],

    "-ETTEN": ["betten", "retten", "ketten", "wetten", "netten", "fetten", "hätten"],
    "-ACHT": ["nacht", "gemacht", "gelacht", "gedacht", "pracht", "tracht", "wacht"],
    "-ÜHLEN": ["kühlen", "fühlen", "spülen", "wühlen", "stühlen"],
    "-AFEN": ["schlafen", "hafen", "strafen", "waffen", "trafen"],
    "-ÄUMEN": ["träumen", "räumen", "säumen", "bäumen", "schäumen"],
    "-ECKEN": ["wecken", "strecken", "lecken", "recken", "stecken", "necken", "schrecken"],

    "-ECHEN": ["stechen", "brechen", "sprechen", "rächen", "schwächen"],
    "-REITEN": ["reiten", "streiten", "leiten", "breiten", "weiten", "gleiten", "schreiten"],
    "-OSSEN": ["stoßen", "schossen", "flossen", "genossen", "verdrossen", "geschlossen"],
    "-TIMMEN": ["stimmen", "schwimmen", "trimmen", "klimmen", "nimmt"],
    "-AUCHEN": ["tauchen", "rauchen", "brauchen", "hauchen", "schnauchen"],
}

DERB_POTENZIAL = {
    "stechen", "reiten", "graben", "lecken", "bocken", "stoßen", "rauchen",
    "schwitzen", "wühlen", "stange", "gurke", "bulle", "wurst", "reiter",
    "becher", "hammel", "strümpfe", "mähre", "rammel", "triebe", "liebe",
    "lust", "brust", "kuss", "saufen", "raufen", "arsche", "pfeifen",
    "schlange", "locken", "hinten", "fühlen", "wühlen", "treiben", "reiben",
    "sitzen", "schwimmen", "gammel", "bockeln", "küssen", "saugen",
    "schwitzen", "schlafen", "träumen", "wetten", "strafen", "tauchen",
    "rauchen", "lecken", "stechen", "sprechen", "brechen", "hiebe",
    "diebe", "triebe", "blieben", "geboten", "streifen", "schleifen",
    "platzen", "knacken", "greifen", "zungen", "stützen", "nützen",
}


def get_all_words():
    seen = set()
    words = []
    for thema, ws in THEMEN_WOERTER.items():
        for w in ws:
            if w not in seen:
                seen.add(w)
                words.append(w)
    return words


def get_thema_for_word(word):
    for thema, ws in THEMEN_WOERTER.items():
        if word in ws:
            return thema
    return "Sonstige"


def load_progress():
    if SESSION_FILE.exists():
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"completed": [], "total_pairs": 0, "last_run": None}


def save_progress(progress):
    progress["last_run"] = datetime.now().isoformat()
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def random_delay(min_s=3.0, max_s=8.0):
    base = random.uniform(min_s, max_s)
    jitter = random.uniform(0, 2.0)
    time.sleep(base + jitter)


def build_session():
    session = requests.Session()
    session.headers.update({
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    })
    return session


def rotate_headers(session):
    ua = random.choice(USER_AGENTS)
    ref = random.choice(REFERERS)
    session.headers["User-Agent"] = ua
    session.headers["Referer"] = ref


def extract_json_from_html(html_text):
    bt_idx = html_text.find("bootstrapTable")
    if bt_idx < 0:
        return None

    data_marker = "data: ["
    data_idx = html_text.find(data_marker, bt_idx)
    if data_idx < 0:
        data_marker = "data:["
        data_idx = html_text.find(data_marker, bt_idx)
    if data_idx < 0:
        return None

    bracket_start = html_text.find("[", data_idx)
    depth = 0
    in_string = False
    escape_next = False

    for i in range(bracket_start, len(html_text)):
        ch = html_text[i]
        if escape_next:
            escape_next = False
            continue
        if ch == "\\":
            escape_next = True
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                json_str = html_text[bracket_start : i + 1]
                try:
                    data = json.loads(json_str)
                    if isinstance(data, list) and len(data) > 0 and "begriff" in data[0]:
                        return data
                except (json.JSONDecodeError, IndexError):
                    pass
                break

    return None


def fetch_rhymes(session, word, precision=2, limit_common=True, max_retries=3):
    # WICHTIG: Einige Wörter (z.B. "abendrot") scheinen auf der Seite keine Reime mit `limit_term=1` zu finden, 
    # oder die Seite blockiert bestimmte Parameter-Kombinationen.
    # Lyrikecke gibt dann fälschlicherweise die leere Startseite zurück (ohne bootstrapTable) statt eines echten Fehlers.
    # Wir passen die Payload so an, dass sie robuster ist.
    
    data = {
        "rhyme_term": word,
        "rhyme_precision": str(precision),
    }
    
    if limit_common:
        # Die Checkbox auf der Website ist nicht limit_term, sondern limit_rare und limit_term
        # Wir testen, ob es klappt, wenn wir limit_term auf "0" setzen für problematische Wörter, 
        # aber wir versuchen es zuerst mit "1"
        data["rhyme_limit_term"] = "1"

    for attempt in range(max_retries):
        try:
            rotate_headers(session)
            # Sicherstellen dass wir die richtigen POST Headers haben
            session.headers.update({"Content-Type": "application/x-www-form-urlencoded"})
            
            resp = session.post(
                BASE_URL,
                data=data,
                timeout=30,
                allow_redirects=True,
            )
            resp.raise_for_status()

            rhymes = extract_json_from_html(resp.text)
            if rhymes is not None:
                return rhymes

            # Fallback: Wenn es keine Reime gab (leere Startseite returned),
            # probieren wir es im nächsten Versuch ohne `rhyme_limit_term` (also "Ableitungen einschließen")
            if attempt == 0:
                data["rhyme_limit_term"] = "0"
            elif attempt == 1:
                # Versuch es mit Normal-Precision (1 statt 2)
                data["rhyme_precision"] = "1"

            if attempt < max_retries - 1:
                wait = (attempt + 1) * 5 + random.uniform(1, 3)
                print(f"    Kein JSON in Antwort, versuche andere Parameter. Retry in {wait:.1f}s...")
                time.sleep(wait)

        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 10 + random.uniform(2, 5)
                print(f"    Fehler: {e}, retry in {wait:.1f}s...")
                time.sleep(wait)
            else:
                print(f"    FEHLER nach {max_retries} Versuchen: {e}")
                return None

    return None


MIN_LAENDLICH = 3

_UNBETONT = re.compile(r'(?:en|e[rsm]?)$')

def extract_klang(wort):
    w = wort.lower().strip()
    if len(w) <= 3:
        return w
    w_stamm = _UNBETONT.sub('', w)
    if len(w_stamm) < 2:
        w_stamm = w
    match = re.search(r'([aeiouäöü]{1,3}[^aeiouäöü]*)$', w_stamm)
    if match:
        return match.group(1)
    return w_stamm[-3:] if len(w_stamm) >= 3 else w_stamm


def build_gruppe(word, rhymes, min_rating=80, min_wert=10):
    filtered = [r for r in rhymes if r.get("rating", {}).get("rating", 0) >= min_rating and r.get("wert", 0) >= min_wert]
    filtered.sort(key=lambda r: r.get("rating", {}).get("rating", 0), reverse=True)
    
    top_n = min(30, len(filtered))
    top_rhymes = filtered[:top_n]
    
    thema = get_thema_for_word(word)

    gruppen_dict = {}
    for r in top_rhymes:
        begriff = r.get("begriff", "")
        if not begriff:
            continue
            
        silben = r.get("silben", 0)
        klang = extract_klang(begriff)
        
        key = f"{klang}_{silben}"
        
        if key not in gruppen_dict:
            gruppen_dict[key] = {
                "klang": klang,
                "suchwort": word,
                "silben": silben,
                "thema": thema,
                "woerter": set()
            }
        
        gruppen_dict[key]["woerter"].add(begriff)

    gruppen_liste = []
    for g in gruppen_dict.values():
        if len(g["woerter"]) >= MIN_LAENDLICH:
            g["woerter"] = list(g["woerter"])
            gruppen_liste.append(g)
            
    return gruppen_liste


def save_pairs(pairs, filepath):
    with open(filepath, "a", encoding="utf-8") as f:
        for pair in pairs:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")

def import_json_data(json_data):
    """
    Imports an array of JSON groups back into the scraper's internal JSONL format.
    Checks for duplicates based on klang and suchwort, and updates progress.json.
    """
    jsonl_path = RESULTS_FILE
    existing_groups = set()
    
    # Load existing to avoid duplicates
    if jsonl_path.exists():
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    g = json.loads(line)
                    key = f"{g.get('klang', '')}_{g.get('suchwort', '')}"
                    existing_groups.add(key)
                    
    new_groups = []
    imported_words = set()
    
    for item in json_data:
        # Check if it's the new groups format
        klang = item.get("klang", "")
        suchwort = item.get("suchwort", "")
        
        if klang and suchwort:
            key = f"{klang}_{suchwort}"
            
            if key not in existing_groups:
                existing_groups.add(key)
                
                # Make sure the group structure is clean
                internal_group = {
                    "klang": klang,
                    "suchwort": suchwort,
                    "silben": item.get("silben", 0),
                    "thema": item.get("thema", "Unbekannt"),
                    "woerter": item.get("woerter", []),
                    "kontext": item.get("kontext", [])
                }
                new_groups.append(internal_group)
                imported_words.add(suchwort)
                    
    if new_groups:
        save_pairs(new_groups, jsonl_path)
        export_json() # Update the export file
        
        # Update progress
        progress = load_progress()
        completed = set(progress.get("completed", []))
        completed.update(imported_words)
        progress["completed"] = list(completed)
        progress["total_pairs"] = progress.get("total_pairs", 0) + len(new_groups)
        save_progress(progress)
        
    return len(new_groups)


def run_scraper(words=None, min_rating=80):
    if words is None:
        words = get_all_words()

    progress = load_progress()
    completed = set(progress["completed"])

    remaining = [w for w in words if w not in completed]
    if not remaining:
        print("Alle Wörter bereits abgearbeitet! Lösche progress.json für Neustart.")
        return

    print(f"=== Reim-Scraper ===")
    print(f"Wörter gesamt: {len(words)}")
    print(f"Bereits erledigt: {len(completed)}")
    print(f"Verbleibend: {len(remaining)}")
    print(f"Min. Rating: {min_rating}, Min. Häufigkeit: 10")
    print(f"Silben-Match: NUR gleiche Silbenanzahl pro Paar")
    print(f"Output: {RESULTS_FILE}")
    print(f"========================\n")

    session = build_session()

    try:
        session.get("https://www.lyrikecke.de/", timeout=15)
        time.sleep(random.uniform(2, 4))
    except Exception:
        print("Konnte Cookie-Seite nicht laden, fahre trotzdem fort...")

    total_new_pairs = 0
    errors = 0

    for idx, word in enumerate(remaining):
        print(f"[{idx+1}/{len(remaining)}] '{word}' ({get_thema_for_word(word)})")

        rhymes = fetch_rhymes(session, word)

        if rhymes is None:
            errors += 1
            print(f"    ÜBERSPRUNGEN (Fehler)\n")
            if errors > 5:
                long_pause = random.uniform(60, 120)
                print(f"  >> Zu viele Fehler, pause {long_pause:.0f}s...")
                time.sleep(long_pause)
                errors = 0
            continue

        gruppen = build_gruppe(word, rhymes, min_rating=min_rating)

        if gruppen:
            thema = gruppen[0].get("thema", "")
            
            # 2. Direkt nach dem Scrapen: KI-Evaluation
            for gruppe in gruppen:
                bewertete_woerter = []
                woerter = gruppe.get("woerter", [])
                
                for chunk in chunk_list(woerter, BATCH_SIZE):
                    bewertet = evaluate_woerter(chunk, thema)
                    bewertete_woerter.extend(bewertet)
                    
                gruppe["woerter"] = bewertete_woerter

                hat_derb = any(w.get("derb") or w.get("pointe") for w in bewertete_woerter)
                gruppe["derb_potenzial"] = hat_derb
                gruppe["pointe_silbe"] = gruppe["klang"] if hat_derb else ""
                
            # Kontext einmal pro Suchwort generieren und an jede Gruppe hängen
            kontext = generate_kontext(word, thema)
            for gruppe in gruppen:
                gruppe["kontext"] = kontext
                
            # Speichern
            save_pairs(gruppen, RESULTS_FILE)
            total_new_pairs += len(gruppen)
            print(f"    {len(rhymes)} Reime, {len(gruppen)} Gruppen gespeichert")
        else:
            print(f"    {len(rhymes)} Reime, keine guten Gruppen")

        progress["completed"].append(word)
        progress["total_pairs"] = progress.get("total_pairs", 0) + len(gruppen)
        save_progress(progress)

        if (idx + 1) % 5 == 0:
            print(f"\n  --- Pause ---")
            time.sleep(random.uniform(8, 15))
            print(f"  --- Weiter ---\n")
        else:
            random_delay()

    export_json()
    print(f"\n=== FERTIG ===")
    print(f"Neue Paare: {total_new_pairs}")
    print(f"Gesamt: {progress['total_pairs']}")
    print(f"Output: {RESULTS_FILE}")


def export_json():
    jsonl_path = RESULTS_FILE
    json_path = JSON_EXPORT_FILE

    if not jsonl_path.exists():
        print("Keine Daten gefunden.")
        return []

    gruppen = []
    seen = set()
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                g = json.loads(line)
                key = f"{g.get('klang', '')}_{g.get('suchwort', '')}"
                if key not in seen:
                    seen.add(key)
                    gruppen.append(g)

    merged = _merge_duplicate_gruppen(gruppen)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print(f"JSON exportiert: {json_path} ({len(merged)} Gruppen, {len(gruppen)} vor Merge)")
    return merged


def _merge_duplicate_gruppen(gruppen):
    merged_dict = {}
    for g in gruppen:
        wort_namen = sorted(w.get("wort", "").lower() if isinstance(w, dict) else str(w).lower() for w in g.get("woerter", []))
        merge_key = tuple(wort_namen)

        if merge_key not in merged_dict:
            merged_dict[merge_key] = g.copy()
            merged_dict[merge_key]["suchwoerter"] = [g.get("suchwort", "")]
            continue

        existing = merged_dict[merge_key]
        sw = g.get("suchwort", "")
        if sw and sw not in existing["suchwoerter"]:
            existing["suchwoerter"].append(sw)

        existing_woerter = {w.get("wort", "").lower() for w in existing.get("woerter", [])}

        for w in g.get("woerter", []):
            wort_name = w.get("wort", "")
            if wort_name.lower() not in existing_woerter:
                existing["woerter"].append(w)
                existing_woerter.add(wort_name.lower())

        existing_kontext = set(k.lower() for k in existing.get("kontext", []))
        for k in g.get("kontext", []):
            if k.lower() not in existing_kontext and len(existing.get("kontext", [])) < 5:
                existing.setdefault("kontext", []).append(k)
                existing_kontext.add(k.lower())

        if len(existing.get("kontext", [])) > 5:
            existing["kontext"] = existing["kontext"][:5]

        if g.get("derb_potenzial"):
            existing["derb_potenzial"] = True
        if g.get("pointe_silbe") and not existing.get("pointe_silbe"):
            existing["pointe_silbe"] = g["pointe_silbe"]

    for g in merged_dict.values():
        if len(g.get("suchwoerter", [])) == 1:
            g["suchwort"] = g["suchwoerter"][0]
        else:
            g["suchwort"] = g["suchwoerter"][0]
        del g["suchwoerter"]

    filtered = []
    for g in merged_dict.values():
        unique_woerter = {}
        for w in g.get("woerter", []):
            wort = str(w.get("wort", "")).strip()
            if not is_valid_word(wort):
                print(f"removed_by_validation: {g.get('suchwort', '')} -> {wort}")
                continue

            score = safe_score(w.get("pointe_score"))
            derb = bool(w.get("derb", False))
            if not (score >= 4 or (score == 3 and derb)):
                print(f"removed_by_score: {g.get('suchwort', '')} -> {wort}")
                continue
            if score == 3 and is_generic_weak_word(wort):
                print(f"removed_by_generic: {g.get('suchwort', '')} -> {wort}")
                continue

            key = wort.lower()
            candidate = {
                "wort": wort,
                "laendlich": bool(w.get("laendlich", False)),
                "derb": derb,
                "pointe_score": score,
            }
            existing = unique_woerter.get(key)
            if (
                existing is None
                or score > existing["pointe_score"]
                or (score == existing["pointe_score"] and derb and not existing["derb"])
            ):
                unique_woerter[key] = candidate

        contrast_gefiltert = []
        for w in unique_woerter.values():
            score = safe_score(w.get("pointe_score"))
            if score <= 3 and not has_unexpected_or_derb_contrast(g.get("suchwort", ""), w.get("wort", ""), derb=bool(w.get("derb", False)), score=score):
                print(f"removed_by_contrast: {g.get('suchwort', '')} -> {w.get('wort', '')}")
                continue
            contrast_gefiltert.append(w)

        valid_kontext = []
        seen_kontext = set()
        for begriff in g.get("kontext", []):
            if not isinstance(begriff, str):
                continue
            clean = begriff.strip()
            key = clean.lower()
            if key in seen_kontext or not is_valid_word(clean):
                continue
            seen_kontext.add(key)
            valid_kontext.append(clean)
            if len(valid_kontext) == 5:
                break

        g["woerter"] = sorted(
            contrast_gefiltert,
            key=lambda w: (safe_score(w.get("pointe_score")), bool(w.get("derb"))),
            reverse=True
        )
        g["kontext"] = valid_kontext

        if len(g["woerter"]) >= 2 and len(g["kontext"]) == 5:
            filtered.append(g)

    verworfen = len(merged_dict) - len(filtered)
    print(f"  Exportiert: {len(filtered)} Gruppen")
    print(f"  Verworfen: {verworfen} Gruppen (kein Pointe-Kandidat)")

    return filtered


def export_csv():
    jsonl_path = RESULTS_FILE
    csv_path = OUTPUT_DIR / "reimpaare.csv"

    if not jsonl_path.exists():
        print("Keine Daten.")
        return

    pairs = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                pairs.append(json.loads(line))

    with open(csv_path, "w", encoding="utf-8-sig") as f:
        f.write("wort_1;wort_2;silben;qualitaet;haeufigkeit;thema;suchwort\n")
        for p in pairs:
            f.write(
                f"{p['reimwort_1']};{p['reimwort_2']};"
                f"{p.get('silben',0)};{p.get('qualitaet',0)};{p.get('haeufigkeit',0)};"
                f"{p.get('thema','')};{p.get('wort','')}\n"
            )

    print(f"CSV exportiert: {csv_path} ({len(pairs)} Paare)")


def load_reimpaare(filepath=None):
    if filepath is None:
        filepath = JSON_EXPORT_FILE
    if not Path(filepath).exists():
        filepath = RESULTS_FILE
    if not Path(filepath).exists():
        return []

    pairs = []
    with open(filepath, "r", encoding="utf-8") as f:
        if str(filepath).endswith(".jsonl"):
            for line in f:
                line = line.strip()
                if line:
                    pairs.append(json.loads(line))
        else:
            pairs = json.load(f)
    return pairs


def get_rhymes_for_word(word, min_rating=80):
    session = build_session()
    rhymes = fetch_rhymes(session, word)
    if rhymes is None:
        return []
    gruppen = build_gruppe(word, rhymes, min_rating=min_rating)
    return gruppen


def get_rhyme_pairs(words=None, min_rating=80):
    if words is None:
        words = get_all_words()

    all_pairs = []
    session = build_session()

    for idx, word in enumerate(words):
        print(f"[{idx+1}/{len(words)}] '{word}'...")
        rhymes = fetch_rhymes(session, word)
        if rhymes is None:
            continue
        pairs = build_rhyme_pairs(word, rhymes, min_rating=min_rating)
        all_pairs.extend(pairs)
        if idx < len(words) - 1:
            random_delay()

    print(f"Fertig: {len(all_pairs)} Paare")
    return all_pairs


if __name__ == "__main__":
    print("=" * 50)
    print("  LYRIKECKE REIM-SCRAPER v2")
    print("  Silben-Match | Themen | Häufigkeitsfilter")
    print("=" * 50)

    custom_words = None
    min_rating = 80

    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == "--csv":
            export_csv()
            sys.exit(0)
        elif arg == "--json":
            export_json()
            sys.exit(0)
        elif arg == "--words":
            custom_words = sys.argv[2:]
        elif arg == "--file":
            word_file = Path(sys.argv[2])
            if word_file.exists():
                with open(word_file, "r", encoding="utf-8") as f:
                    custom_words = [w.strip() for w in f.readlines() if w.strip()]
                print(f"{len(custom_words)} Wörter aus {word_file} geladen")
            else:
                print(f"Datei nicht gefunden: {word_file}")
                sys.exit(1)
        elif arg == "--rating":
            min_rating = int(sys.argv[2])
        elif arg == "--help":
            print("Usage:")
            print("  python reim_scraper.py                    # Alle Themen-Wörter")
            print("  python reim_scraper.py --words wort1 wort2 # Eigene Wörter")
            print("  python reim_scraper.py --file woerter.txt  # Wörter aus Datei")
            print("  python reim_scraper.py --json              # Exportiere als JSON")
            print("  python reim_scraper.py --csv               # Exportiere als CSV")
            print("  python reim_scraper.py --rating 100        # Min. Rating-Schwelle")
            sys.exit(0)

    run_scraper(words=custom_words, min_rating=min_rating)
