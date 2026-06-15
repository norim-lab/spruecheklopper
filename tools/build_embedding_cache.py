"""build_embedding_cache.py — Offline-Precompute der BGE-M3 Embeddings.

Liest output/reimgruppen_derb.jsonl, sammelt ALLE seeds + partner-Woerter
(dedupliziert) und holt BGE-M3-Vektoren in BATCHES via DeepInfra.
Schreibt sie nach output/embedding_cache.json (gleiches Format, das
_load_embedding_cache in generator.py liest).

Idempotent: bereits gecachte Woerter werden uebersprungen.

Usage:
    python tools/build_embedding_cache.py
    python tools/build_embedding_cache.py --batch-size 50    # kleinere Batches
"""

import json
import os
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    sys.stderr.write("FEHLER: requests nicht installiert.\n")
    sys.exit(1)

# ── Pfade ──
ROOT = Path(__file__).resolve().parent.parent
JSONL_PATH = ROOT / "output" / "reimgruppen_derb.jsonl"
CACHE_PATH = ROOT / "output" / "embedding_cache.json"
CONFIG_PATH = ROOT / "config.json"
EMBEDDING_URL = "https://api.deepinfra.com/v1/inference/BAAI/bge-m3"
BATCH_SIZE = 100


def read_api_key():
    """Liest den DeepInfra API-Key aus ENV oder config.json."""
    key = os.environ.get("DEEPINFRA_API_KEY", "")
    if key:
        return key
    if CONFIG_PATH.exists():
        try:
            return json.load(open(CONFIG_PATH, encoding="utf-8")).get(
                "deepinfra_api_key", "")
        except Exception:
            pass
    return ""


def collect_words():
    """Sammelt alle seeds + woerter aus reimgruppen_derb.jsonl (dedupliziert,
    lowercase)."""
    words = set()
    n_groups = 0
    with open(JSONL_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                g = json.loads(line)
            except Exception:
                continue
            n_groups += 1
            seed = g.get("seed", "")
            if seed:
                words.add(seed.lower())
            for w in g.get("woerter", []):
                wort = w.get("wort", "")
                if wort:
                    words.add(wort.lower())
    return words, n_groups


def load_cache():
    """Laedt den existierenden Cache."""
    if CACHE_PATH.exists():
        try:
            return json.load(open(CACHE_PATH, encoding="utf-8"))
        except Exception as e:
            sys.stderr.write("Cache-Lesefehler: " + str(e) + " – starte leer\n")
    return {}


def embed_batch(words, api_key):
    """Holt BGE-M3-Vektoren fuer eine Wortliste. Gibt dict {wort: vec} oder
    None bei Fehler zurueck."""
    headers = {"Authorization": "Bearer " + api_key,
               "Content-Type": "application/json"}
    body = {"inputs": list(words)}
    try:
        r = requests.post(EMBEDDING_URL, headers=headers, json=body, timeout=60)
        if r.status_code == 429:
            sys.stderr.write("  Rate-Limit – warte 10s ...\n")
            time.sleep(10)
            return None
        r.raise_for_status()
        data = r.json()
        vecs = data.get("data") or data.get("embeddings") or []
        if len(vecs) != len(words):
            sys.stderr.write(
                "  Anzahl ungleich (" + str(len(vecs)) + "/"
                + str(len(words)) + ") – skippe Batch\n")
            return None
        return {w: v for w, v in zip(words, vecs)}
    except Exception as e:
        sys.stderr.write("  API-Fehler: " + str(e) + "\n")
        return None


def main():
    batch_size = BATCH_SIZE
    if "--batch-size" in sys.argv:
        idx = sys.argv.index("--batch-size")
        if idx + 1 < len(sys.argv):
            batch_size = int(sys.argv[idx + 1])

    print("=" * 60)
    print("build_embedding_cache.py – BGE-M3 Offline-Precompute")
    print("=" * 60)

    # API-Key
    api_key = read_api_key()
    if not api_key:
        sys.stderr.write("FEHLER: Kein DeepInfra API-Key gefunden.\n")
        sys.stderr.write("Setze DEEPINFRA_API_KEY oder trage 'deepinfra_api_key'"
                         " in config.json ein.\n")
        sys.exit(1)
    print("API-Key gefunden.")

    # Woerter sammeln
    print("Lese " + str(JSONL_PATH.name) + " ...")
    all_words, n_groups = collect_words()
    print("  " + str(n_groups) + " Gruppen, "
          + str(len(all_words)) + " unique Woerter (lowercase)")

    # Cache laden
    cache = load_cache()
    n_cached = len(cache)
    print("Existierender Cache: " + str(n_cached) + " Woerter")

    # Fehlende Woerter
    missing = sorted(w for w in all_words if w not in cache)
    print("Fehlend: " + str(len(missing)) + " Woerter")
    if not missing:
        print("Cache ist bereits vollstaendig. Nichts zu tun.")
        print("=" * 60)
        return

    # Batches embedden
    n_batches = (len(missing) + batch_size - 1) // batch_size
    print("Starte " + str(n_batches) + " API-Call(s) "
          "(batch_size=" + str(batch_size) + ") ...")
    print("-" * 60)

    t0 = time.time()
    api_calls = 0
    newly_embedded = 0
    errors = 0

    for i in range(0, len(missing), batch_size):
        batch = missing[i:i + batch_size]
        batch_num = i // batch_size + 1
        print("  Batch " + str(batch_num) + "/" + str(n_batches)
              + " (" + str(len(batch)) + " Woerter) ...", end="", flush=True)
        result = embed_batch(batch, api_key)
        if result is None:
            # Retry einmal nach kurzer Pause
            time.sleep(3)
            result = embed_batch(batch, api_key)
        if result is None:
            print(" FEHLER (skip)")
            errors += 1
            continue
        cache.update(result)
        newly_embedded += len(result)
        api_calls += 1
        print(" OK (" + str(len(result)) + " Vektoren)")

    elapsed = time.time() - t0

    # Cache schreiben
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    json.dump(cache, open(CACHE_PATH, "w", encoding="utf-8"))
    cache_size = os.path.getsize(CACHE_PATH)

    # Report
    print("-" * 60)
    print("FERTIG.")
    print("  Woerter total im Cache : " + str(len(cache)))
    print("  Neu hinzugefuegt      : " + str(newly_embedded))
    print("  API-Calls             : " + str(api_calls))
    print("  Errors (uebersprungen) : " + str(errors))
    print("  Dauer                 : " + str(round(elapsed, 1)) + "s")
    print("  Cache-Dateigroesse    : "
          + str(round(cache_size / 1024 / 1024, 1)) + " MB")
    print("=" * 60)


if __name__ == "__main__":
    main()
