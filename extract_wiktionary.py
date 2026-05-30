"""
Extrahiert Wiktionary-Daten fuer alle Woerter in unserem Snapshot.
Ausgabe: wiktionary_data.json mit Wortart, Synonymen, Antonymen, 
         semantischen Feldern, Ober-/Unterbegriffen, verwandten Woertern.
"""
import json
import sqlite3
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(r"C:\Users\miron\Documents\trae_projects\scrape")
OUTPUT_DIR = BASE_DIR / "output"
SNAPSHOT_F = OUTPUT_DIR / "sprachnudel_raw.snapshot.v11.merged.jsonl"
DB_PATH = OUTPUT_DIR / "wiktionary" / "de_wiktionary_normalized.db"
OUT_F = OUTPUT_DIR / "wiktionary_data.json"

print("=== Wiktionary Daten-Extraktion ===\n")

# 1. Alle Woerter aus unserem Snapshot laden
print("Lade Snapshot-Woerter...")
snapshot_words = {}
with open(SNAPSHOT_F, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        entry = json.loads(line)
        word = entry.get("suchwort", "")
        if word:
            snapshot_words[word.casefold()] = word
print(f"  {len(snapshot_words)} Woerter geladen")

# 2. Alle Ergebnis-Woerter sammeln
print("Sammle Ergebnis-Woerter aus Reimen...")
result_words = set()
with open(SNAPSHOT_F, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        entry = json.loads(line)
        for r in entry.get("results", []):
            w = r.get("wort", "")
            if w:
                result_words.add(w.casefold())
print(f"  {len(result_words)} Ergebnis-Woerter")

# Alle Woerter die wir nachschlagen wollen
all_words = set(snapshot_words.keys()) | result_words
print(f"  Gesamt: {len(all_words)} eindeutige Woerter fuer Wiktionary-Lookup\n")

# 3. Wiktionary DB oeffnen
if not DB_PATH.exists():
    print(f"FEHLER: DB nicht gefunden unter {DB_PATH}")
    print("Bitte zuerst herunterladen!")
    exit(1)

print(f"Oeffne Wiktionary DB: {DB_PATH}")
conn = sqlite3.connect(str(DB_PATH))
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA cache_size=-500000")  # 500MB Cache
cur = conn.cursor()

# 4. POS-Mapping (Wiktionary pos -> Deutsch)
POS_MAP = {
    "noun": "Substantiv",
    "verb": "Verb",
    "adj": "Adjektiv",
    "adv": "Adverb",
    "pron": "Pronomen",
    "prep": "Praeposition",
    "conj": "Konjunktion",
    "interj": "Interjektion",
    "part": "Partikel",
    "num": "Numerale",
    "article": "Artikel",
}

def map_pos(pos):
    if not pos:
        return None
    return POS_MAP.get(pos, pos)

# 5. Batch-Lookup vorbereiten

# Eintraege-Lookup: word -> (id, pos)
print("Lade Wiktionary-Eintraege...")
entry_lookup = {}  # casefold word -> list of (id, pos)
cur.execute("SELECT id, word, pos FROM entries WHERE lang = 'Deutsch'")
for row in cur:
    eid, word, pos = row
    if word:
        key = word.casefold()
        if key not in entry_lookup:
            entry_lookup[key] = []
        entry_lookup[key].append((eid, pos))
print(f"  {len(entry_lookup)} Deutsche Wiktionary-Eintraege indexiert")

# Synonyme laden
print("Lade Synonyme...")
synonyms = defaultdict(list)
cur.execute("""
    SELECT e.word, s.synonym_word 
    FROM synonyms s 
    JOIN entries e ON s.entry_id = e.id 
    WHERE e.lang = 'Deutsch'
""")
for row in cur:
    word, syn = row
    if word and syn:
        synonyms[word.casefold()].append(syn)
print(f"  {len(synonyms)} Woerter mit Synonymen")

# Antonyme laden
print("Lade Antonyme...")
antonyms = defaultdict(list)
cur.execute("""
    SELECT e.word, a.antonym_word 
    FROM antonyms a 
    JOIN entries e ON a.entry_id = e.id 
    WHERE e.lang = 'Deutsch'
""")
for row in cur:
    word, ant = row
    if word and ant:
        antonyms[word.casefold()].append(ant)
print(f"  {len(antonyms)} Woerter mit Antonymen")

# Verwandte Woerter
print("Lade verwandte Woerter...")
related = defaultdict(list)
cur.execute("""
    SELECT e.word, r.related_word 
    FROM related_terms r 
    JOIN entries e ON r.entry_id = e.id 
    WHERE e.lang = 'Deutsch'
""")
for row in cur:
    word, rel = row
    if word and rel:
        related[word.casefold()].append(rel)
print(f"  {len(related)} Woerter mit verwandten Termen")

# Abgeleitete Woerter
print("Lade abgeleitete Woerter...")
derived = defaultdict(list)
cur.execute("""
    SELECT e.word, d.derived_word 
    FROM derived_terms d 
    JOIN entries e ON d.entry_id = e.id 
    WHERE e.lang = 'Deutsch'
""")
for row in cur:
    word, der = row
    if word and der:
        derived[word.casefold()].append(der)
print(f"  {len(derived)} Woerter mit abgeleiteten Termen")

# Semantische Felder / Topics
print("Lade semantische Felder (Topics)...")
topics = defaultdict(list)
cur.execute("""
    SELECT e.word, t.topic
    FROM entries e
    JOIN senses s ON e.id = s.entry_id
    JOIN sense_topics st ON s.id = st.sense_id
    JOIN topics t ON st.topic_id = t.id
    WHERE e.lang = 'Deutsch'
""")
seen = set()
for row in cur:
    word, topic = row
    if word and topic:
        key = (word.casefold(), topic)
        if key not in seen:
            seen.add(key)
            topics[word.casefold()].append(topic)
print(f"  {len(topics)} Woerter mit Topics")

# Kategorien
print("Lade Kategorien...")
categories = defaultdict(list)
cur.execute("""
    SELECT e.word, c.category
    FROM entries e
    JOIN entry_categories ec ON e.id = ec.entry_id
    JOIN categories c ON ec.category_id = c.id
    WHERE e.lang = 'Deutsch'
""")
cat_seen = set()
for row in cur:
    word, cat = row
    if word and cat:
        key = (word.casefold(), cat)
        if key not in cat_seen:
            cat_seen.add(key)
            categories[word.casefold()].append(cat)
print(f"  {len(categories)} Woerter mit Kategorien")

# IPA Aussprache
print("Lade IPA Aussprache...")
ipa_map = defaultdict(list)
cur.execute("""
    SELECT e.word, s.ipa
    FROM entries e
    JOIN sounds s ON e.id = s.entry_id
    WHERE e.lang = 'Deutsch' AND s.ipa IS NOT NULL
""")
ipa_seen = set()
for row in cur:
    word, ipa = row
    if word and ipa:
        key = (word.casefold(), ipa)
        if key not in ipa_seen:
            ipa_seen.add(key)
            ipa_map[word.casefold()].append(ipa)
print(f"  {len(ipa_map)} Woerter mit IPA")

# Definitionen (nur erste 3)
print("Lade Definitionen...")
definitions = defaultdict(list)
cur.execute("""
    SELECT e.word, g.gloss_text
    FROM entries e
    JOIN senses se ON e.id = se.entry_id
    JOIN glosses g ON se.id = g.sense_id
    WHERE e.lang = 'Deutsch' AND g.gloss_text IS NOT NULL
""")
def_seen = set()
for row in cur:
    word, gloss = row
    if word and gloss:
        key = word.casefold()
        if len(definitions[key]) < 3 and (key, gloss) not in def_seen:
            def_seen.add((key, gloss))
            definitions[key].append(gloss)
print(f"  {len(definitions)} Woerter mit Definitionen")

conn.close()

# 6. Lookup-Funktion
def get_wiktionary_data(word_cf: str) -> dict:
    """Extrahiert alle Wiktionary-Daten fuer ein Wort."""
    result = {}
    
    # Wortart
    entries = entry_lookup.get(word_cf, [])
    if entries:
        pos_list = list(set(map_pos(pos) for _, pos in entries if pos))
        result["wortart"] = pos_list[0] if len(pos_list) == 1 else pos_list
    
    # Synonyme
    syns = synonyms.get(word_cf, [])
    if syns:
        result["synonyme"] = list(dict.fromkeys(syns))[:20]
    
    # Antonyme
    ants = antonyms.get(word_cf, [])
    if ants:
        result["antonyme"] = list(dict.fromkeys(ants))[:10]
    
    # Verwandte
    rels = related.get(word_cf, [])
    if rels:
        result["verwandte"] = list(dict.fromkeys(rels))[:20]
    
    # Abgeleitete
    ders = derived.get(word_cf, [])
    if ders:
        result["abgeleitete"] = list(dict.fromkeys(ders))[:20]
    
    # Semantische Felder
    tops = topics.get(word_cf, [])
    if tops:
        result["themen"] = list(dict.fromkeys(tops))
    
    # Kategorien (gefiltert)
    cats = categories.get(word_cf, [])
    if cats:
        # Nur relevante Kategorien, nicht hunderte
        relevant = [c for c in cats if not c.startswith("Wiktionary:") and len(c) < 60]
        result["kategorien_wiktionary"] = list(dict.fromkeys(relevant))[:10]
    
    # IPA
    ipas = ipa_map.get(word_cf, [])
    if ipas:
        result["ipa"] = list(dict.fromkeys(ipas))[:3]
    
    # Definitionen
    defs = definitions.get(word_cf, [])
    if defs:
        result["definitionen"] = defs
    
    return result

# 7. Daten fuer alle unsere Woerter extrahieren
print(f"\nExtrahiere Wiktionary-Daten fuer {len(all_words)} Woerter...")
wiktionary_data = {}
found = 0
with_pos = 0
with_syn = 0
with_topics = 0

for word_cf in all_words:
    data = get_wiktionary_data(word_cf)
    if data:
        wiktionary_data[word_cf] = data
        found += 1
        if "wortart" in data:
            with_pos += 1
        if "synonyme" in data:
            with_syn += 1
        if "themen" in data:
            with_topics += 1

print(f"\nErgebnis:")
print(f"  Im Wiktionary gefunden: {found}/{len(all_words)} ({100*found/len(all_words):.1f}%)")
print(f"  Mit Wortart: {with_pos}")
print(f"  Mit Synonymen: {with_syn}")
print(f"  Mit Themen: {with_topics}")

# 8. Speichern
print(f"\nSpeichere nach {OUT_F}...")
with open(OUT_F, "w", encoding="utf-8") as f:
    json.dump(wiktionary_data, f, ensure_ascii=False)

size_mb = OUT_F.stat().st_size / 1024 / 1024
print(f"Fertig! {size_mb:.1f} MB gespeichert.")
